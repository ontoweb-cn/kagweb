"""Which identity a KAGWeb turn runs as on a remote agent service.

KAGWeb is the identity provider for its own users; the remote service has its
own accounts. This module decides how the two are bridged, and it is the only
place that decides, so the answer is the same for every HTTP backend.

Two bridges, and they are not equivalent:

**Attribution** (``identity_mode="header"``) presents the calling account as the
service's ``X-Intellect-User`` header while still authenticating with the
deployment-wide service key. It makes each turn *belong* to a user — the service
records who owns the session and run — but it is **not** an access boundary: the
service key carries an unrestricted principal, so anything holding it can reach
any session or run. KAGWeb's own session store remains the isolation boundary.

**Delegation** (``identity_mode="token"``) presents the user's *own* member
token, so the service applies its own role checks and per-owner isolation to
that user. This is the mode that actually enforces anything on the far side.

The default is ``off``: a deployment that upgrades KAGWeb must keep sending the
requests it sent before, and identity headers are not free — they change what
the service records about every turn.

Linkage failures never downgrade. ``token`` mode lets an *unlinked* user fall
back to attribution, but a user whose link exists and has gone bad (expired,
revoked, or rejected by the service) gets an error instead: the fallback is the
service key, which is *more* privileged than the token that just failed, so
"helpfully" downgrading would hand the user an escalation exactly when the
restriction started to matter. ``token_required`` removes even the unlinked
fallback.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import logging
import time
from typing import Any
from urllib.parse import urlsplit

from kagweb.services.i18n import t

from .identity_store import IntellectIdentityStore, LinkedIdentity

logger = logging.getLogger(__name__)

#: ``off``            — send nothing extra (the pre-existing behaviour).
#: ``header``         — attribution: the account rides as ``X-Intellect-User``.
#: ``token``          — delegation, falling back to attribution when unlinked.
#: ``token_required`` — delegation only; an unlinked user cannot start a turn.
IDENTITY_MODES = ("off", "header", "token", "token_required")

DEFAULT_IDENTITY_MODE = "off"

#: The service accepts ``mem_`` + ``[A-Za-z0-9_-]{1,60}``, total length 5..64.
#: Values outside that are silently dropped by the service, which would look
#: exactly like attribution not working.
_MEMBER_ID_PREFIX = "mem_"
_MEMBER_ID_MAX_BODY = 60

_HEADER_USER = "X-Intellect-User"
_HEADER_TEAM = "X-Intellect-Team"
_HEADER_PROJECT = "X-Intellect-Project"


class IdentityUnavailable(RuntimeError):
    """The turn cannot run under the identity its profile requires.

    Raised for a *linked* user whose credential can no longer be used, and for
    an unlinked user under ``token_required``. Carries a message already
    localized for the user.
    """


@dataclass(frozen=True)
class BackendIdentity:
    """The credential and headers one turn should present.

    Resolved values, not fragments: :attr:`api_key` and :attr:`headers` are what
    the backend should send, with the profile's own settings already merged in
    the right order.
    """

    #: Bearer token to send. Empty means "no Authorization header".
    api_key: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    #: Prepended to the session id, so one account's sessions can never collide
    #: with another's on the remote service.
    session_prefix: str = ""
    #: ``off`` | ``header`` | ``member`` — what actually got applied.
    tier: str = "off"
    #: True when a ``token``-mode deployment ran the turn without a link. The
    #: turn is fine; the UI uses this to offer connecting an account.
    degraded: bool = False
    #: The service-side member id, when known. Diagnostics only.
    member_id: str = ""


def normalize_identity_mode(value: Any) -> str:
    """Coerce a stored setting to a known mode; unknown values mean ``off``.

    An unrecognized mode must not become the most permissive one, and a typo
    must not silently enable delegation — so anything unrecognized falls back to
    sending nothing at all.
    """
    mode = str(value or "").strip().lower()
    if mode in IDENTITY_MODES:
        return mode
    if mode:
        logger.warning("agent-loop: unknown identity_mode %r; treating as %r", mode, DEFAULT_IDENTITY_MODE)
    return DEFAULT_IDENTITY_MODE


def member_id_for(user_id: str) -> str:
    """Format a KAGWeb account id as the service's member id, or ``""``.

    The body is sanitized to the accepted alphabet rather than rejected: KAGWeb
    ids are normally ``u_<hex>``, but an id can fall back to a user-chosen
    username (``multi_user.context``), and dropping attribution silently for
    those would be a confusing failure.

    Sanitizing alone is not injective — ``a/b`` and ``a-b`` both fold to
    ``a-b``, so two distinct accounts would be attributed to one service
    principal. When folding actually changed the id, a short digest of the
    original is appended to keep distinct accounts distinct. A verbatim-safe id
    is untouched, which is every ordinary deployment; the check is per-id, not
    per-deployment, so an odd account cannot collide with a normal one either.
    """
    raw = str(user_id or "").strip()
    body = "".join(
        ch if (ch.isascii() and (ch.isalnum() or ch in "_-")) else "-" for ch in raw
    )
    if not body:
        return ""
    if body != raw:
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8]
        # Truncate the readable part so the digest always fits the body limit.
        body = f"{body[: _MEMBER_ID_MAX_BODY - len(digest) - 1]}_{digest}"
    else:
        body = body[:_MEMBER_ID_MAX_BODY]
    candidate = f"{_MEMBER_ID_PREFIX}{body}"
    return candidate if len(candidate) >= 5 else ""


def _is_plaintext_remote(url: Any) -> bool:
    """Whether a URL would send a secret in the clear over a real network.

    ``https`` is fine, and so is ``http`` to loopback — nothing observes that.
    ``http`` to anything else is refused for credential-bearing requests.
    """
    raw = str(url or "").strip()
    if not raw:
        return False
    if raw.lower().startswith("https://"):
        return False
    try:
        host = (urlsplit(raw if "://" in raw else f"//{raw}").hostname or "").lower()
    except ValueError:
        return True
    if not host:
        return True
    if host in {"localhost", "::1", "0.0.0.0"} or host.startswith("127."):
        return False
    return True


def service_origin(url: Any) -> str:
    """Normalized ``scheme://host[:port]`` for a service URL, or ``""``.

    Deliberately drops userinfo, path, query and fragment: two profiles that
    address different paths on the same host are the same credential
    destination, and a URL carrying credentials in the userinfo slot must not
    change the answer.
    """
    raw = str(url or "").strip()
    if not raw:
        return ""
    try:
        parts = urlsplit(raw if "://" in raw else f"//{raw}")
    except ValueError:
        return ""
    if not parts.hostname:
        return ""
    host = parts.hostname.lower()
    # A bare word parses as a hostname but is not a service address; requiring a
    # dot, a colon (port), or the loopback forms keeps "not a url" from being
    # recorded as an origin.
    if "." not in host and ":" not in host and host != "localhost":
        return ""
    port = parts.port
    default = {"http": 80, "https": 443}.get(parts.scheme.lower())
    if port is None or port == default:
        return f"{parts.scheme.lower() or 'http'}://{host}"
    return f"{parts.scheme.lower() or 'http'}://{host}:{port}"


def _same_origin(left: Any, right: Any) -> bool:
    """Whether two service URLs address the same credential destination.

    An unrecorded ``service_origin`` (a link made by an earlier build) is not
    treated as a match: without the binding there is no way to know which
    service the token belongs to, and guessing is the exact leak this guards.
    """
    recorded = service_origin(left)
    current = service_origin(right)
    if not recorded or not current:
        return False
    return recorded == current


def _set_header(headers: dict[str, str], name: str, value: str) -> None:
    """Set a header, replacing any differently-cased existing one.

    Header names are case-insensitive, so a profile that configured
    ``x-intellect-user`` would otherwise ride alongside the resolved value and
    the service would pick a winner by parsing order.
    """
    lowered = name.lower()
    for existing in [key for key in headers if key.lower() == lowered]:
        del headers[existing]
    if value:
        headers[name] = value


def _drop_header(headers: dict[str, str], name: str) -> None:
    lowered = name.lower()
    for existing in [key for key in headers if key.lower() == lowered]:
        del headers[existing]


def _current_user_id() -> str:
    """The calling account's id, from the request context only.

    Never from the turn payload: a caller that could name its own identity
    would be able to attribute its work to somebody else, and the whole point
    of the header is that the service trusts it.
    """
    from kagweb.multi_user.context import get_current_user

    return str(get_current_user().id or "")


def identity_store_for(owner_id: str | None = None) -> IntellectIdentityStore:
    """The credential store for *owner_id*, or for the current account."""
    from kagweb.multi_user.paths import owner_secrets_dir

    if owner_id is None:
        from kagweb.multi_user.paths import get_owner_secrets_dir

        return IntellectIdentityStore(get_owner_secrets_dir())
    return IntellectIdentityStore(owner_secrets_dir(owner_id))


class IdentityLinkError(RuntimeError):
    """A link attempt failed for a reason worth showing the user."""


async def _gateway_url() -> str:
    """The configured agent-service base URL the link should target.

    Read from the primary profile rather than asked for: a link is only
    meaningful against the service this deployment actually talks to, and
    letting a user name an arbitrary host would turn the link form into an
    outbound request to wherever they like.
    """
    from .settings import get_agent_loop_settings, resolve_primary_profile

    profile = resolve_primary_profile(get_agent_loop_settings()) or {}
    url = str(profile.get("url") or "").strip()
    if not url:
        raise IdentityLinkError(t("agent_loop.identity_no_service"))
    return url.rstrip("/")


def _json_object(response: Any, *, language: str = "en") -> dict[str, Any]:
    """Parse a service response body that must be a JSON object.

    Reached-but-wrong-API is a configuration mistake worth naming: a URL
    pointing at a login portal or an ingress page answers 200 with HTML, and
    letting the parser raise would surface as a 500 with no explanation. The
    distinction that matters to the user is "this address is not the agent
    service", not "invalid JSON at column 1".
    """
    try:
        payload = response.json() if response.content else {}
    except Exception as exc:
        raise IdentityLinkError(
            t("agent_loop.identity_not_the_service", language=language)
        ) from exc
    if not isinstance(payload, dict):
        raise IdentityLinkError(t("agent_loop.identity_not_the_service", language=language))
    return payload


async def _member_identity(
    url: str, token: str, *, user_id: str, language: str
) -> LinkedIdentity:
    """Confirm a token with the service and read back who it belongs to.

    Verifying here rather than trusting the caller means a bad or pasted-wrong
    credential is rejected at link time, with a message about the credential —
    instead of being stored and surfacing much later as a failed turn.
    """
    import httpx

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(20.0)) as client:
            response = await client.get(
                f"{url}/api/members/me", headers={"Authorization": f"Bearer {token}"}
            )
    except httpx.HTTPError as exc:
        raise IdentityLinkError(t("agent_loop.identity_unreachable", error=str(exc))) from exc

    if response.status_code in (401, 403):
        raise IdentityLinkError(t("agent_loop.identity_token_rejected", language=language))
    if response.status_code >= 400:
        raise IdentityLinkError(
            t("agent_loop.identity_link_failed", status=response.status_code, language=language)
        )
    body = _json_object(response, language=language)
    member_id = str(body.get("id") or body.get("member_id") or "")
    if not member_id:
        raise IdentityLinkError(t("agent_loop.identity_token_rejected", language=language))
    return LinkedIdentity(
        kagweb_user_id=str(user_id),
        member_id=member_id,
        token=token,
        service_origin=service_origin(url),
        linked_at=time.time(),
    )


async def link_with_password(
    login_name: str, password: str, *, user_id: str, language: str = "en"
) -> LinkedIdentity:
    """Exchange an Intellect account's credentials for a member token.

    The password is used for exactly one request and is never stored, logged, or
    echoed back; only the minted token is kept.
    """
    import httpx

    url = await _gateway_url()
    # A password is the user's own *reusable* credential, not a revocable
    # service key the operator chose to send. Putting it on the wire in the
    # clear would leak something the user cannot rotate as easily as
    # reconnecting, so a plaintext origin is refused here even though the
    # deployment may already send its own key that way. Loopback is allowed:
    # there is no network to observe.
    if _is_plaintext_remote(url):
        raise IdentityLinkError(t("agent_loop.identity_insecure_service", language=language))
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(20.0)) as client:
            response = await client.post(
                f"{url}/api/members/login",
                json={"login_name": login_name, "password": password},
            )
    except httpx.HTTPError as exc:
        raise IdentityLinkError(t("agent_loop.identity_unreachable", error=str(exc))) from exc

    if response.status_code == 401:
        raise IdentityLinkError(t("agent_loop.identity_bad_credentials", language=language))
    if response.status_code == 423:
        raise IdentityLinkError(t("agent_loop.identity_locked", language=language))
    if response.status_code >= 400:
        raise IdentityLinkError(
            t("agent_loop.identity_link_failed", status=response.status_code, language=language)
        )
    body = _json_object(response, language=language)
    token = str(body.get("token") or "")
    member = body.get("member") if isinstance(body.get("member"), dict) else {}
    member_id = str(member.get("id") or body.get("member_id") or "")
    if not token or not member_id:
        raise IdentityLinkError(t("agent_loop.identity_link_failed", status=response.status_code))
    return LinkedIdentity(
        kagweb_user_id=str(user_id),
        member_id=member_id,
        token=token,
        service_origin=service_origin(url),
        linked_at=time.time(),
    )


async def link_with_token(
    token: str, *, user_id: str, language: str = "en"
) -> LinkedIdentity:
    """Link using a token handed out of band (admin-issued, or minted by the
    service's token endpoint). Verified with the service before being stored."""
    clean = str(token or "").strip()
    if not clean:
        raise IdentityLinkError(t("agent_loop.identity_empty_token", language=language))
    url = await _gateway_url()
    return await _member_identity(url, clean, user_id=user_id, language=language)


async def unlink(user_id: str, *, revoke: bool = True) -> bool:
    """Drop the stored link, optionally revoking the token at the service.

    Revocation is best effort: a link removed locally must stay removed even if
    the service cannot be reached, or a user could not disconnect while offline.
    """
    import httpx

    owner = str(user_id or "")
    if not owner:
        return False
    store = identity_store_for(owner)
    record = store.load(owner)
    removed = store.clear()
    if revoke and record is not None and record.token:
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
                await client.post(
                    f"{await _gateway_url()}/api/members/logout",
                    headers={"Authorization": f"Bearer {record.token}"},
                )
        except (httpx.HTTPError, IdentityLinkError) as exc:
            logger.info("intellect identity: could not revoke token on unlink: %s", exc)
    return removed


def load_linked_identity(user_id: str | None = None) -> LinkedIdentity | None:
    """The current account's link, if it has a usable one."""
    owner = str(user_id if user_id is not None else _current_user_id())
    if not owner:
        return None
    try:
        return identity_store_for(owner).load(owner)
    except OSError as exc:
        logger.warning("agent-loop: cannot read linked identity: %s", exc)
        return None


def resolve_backend_identity(
    profile: dict[str, Any],
    *,
    family: str = "http",
    language: str = "en",
    user_id: str | None = None,
    linked: LinkedIdentity | None | object = ...,
) -> BackendIdentity | None:
    """Resolve the identity for one turn, or ``None`` for a non-HTTP backend.

    HTTP-family only: identity travels as headers and a bearer token, which a
    local CLI subprocess has no way to present.

    ``linked`` may be passed in to avoid a second store read; the sentinel
    default means "read it if the mode needs it".
    """
    if family != "http":
        return None

    mode = normalize_identity_mode(profile.get("identity_mode"))
    owner = str(user_id if user_id is not None else _current_user_id())
    headers = {str(key): str(value) for key, value in (profile.get("headers") or {}).items()}
    session_prefix = f"{owner}:" if owner else ""

    if mode == "off":
        # Exactly the request this deployment sent before identity existed.
        return BackendIdentity(
            api_key=str(profile.get("api_key") or ""),
            headers=headers,
            session_prefix="",
            tier="off",
        )

    member_id = member_id_for(owner)
    if mode == "header":
        if member_id:
            _set_header(headers, _HEADER_USER, member_id)
        return BackendIdentity(
            api_key=str(profile.get("api_key") or ""),
            headers=headers,
            session_prefix=session_prefix,
            tier="header",
            member_id=member_id,
        )

    # -- token / token_required --------------------------------------------
    if linked is ...:
        linked = load_linked_identity(owner)
    record = linked if isinstance(linked, LinkedIdentity) else None

    if record is not None:
        # A member token is a bearer credential for ONE deployment, and the
        # profile URL is deployment configuration an admin can repoint (or
        # replace by picking a different primary). Presenting the token to
        # whatever origin the profile now names would ship every linked user's
        # credential to that host on their next turn, so the token is bound to
        # the origin it was minted against. A profile that has moved is treated
        # as unusable rather than silently redirected — and never downgraded to
        # the service key, which is the escalation rule above.
        if not _same_origin(record.service_origin, profile.get("url")):
            raise IdentityUnavailable(
                t(
                    "agent_loop.identity_origin_changed",
                    language=language,
                    member_id=record.member_id,
                )
            )

    if record is None:
        if mode == "token_required":
            raise IdentityUnavailable(t("agent_loop.identity_required", language=language))
        # Never linked: attribution is a genuine step up from nothing, and the
        # deployment asked for delegation only when available.
        if member_id:
            _set_header(headers, _HEADER_USER, member_id)
        return BackendIdentity(
            api_key=str(profile.get("api_key") or ""),
            headers=headers,
            session_prefix=session_prefix,
            tier="header",
            degraded=True,
            member_id=member_id,
        )

    if record.is_expired():
        # Linked, then broken. Falling back to the service key here would grant
        # more reach than the token that just lapsed, so it is an error.
        raise IdentityUnavailable(
            t(
                "agent_loop.identity_expired",
                language=language,
                member_id=record.member_id,
            )
        )

    # Member mode ignores X-Intellect-User (the token decides who this is), so
    # sending one would only be misleading.
    _drop_header(headers, _HEADER_USER)
    _set_header(headers, _HEADER_TEAM, record.team_id)
    _set_header(headers, _HEADER_PROJECT, record.project_id)
    return BackendIdentity(
        api_key=record.token,
        headers=headers,
        session_prefix=session_prefix,
        tier="member",
        member_id=record.member_id,
    )


__all__ = [
    "BackendIdentity",
    "DEFAULT_IDENTITY_MODE",
    "IDENTITY_MODES",
    "IdentityLinkError",
    "IdentityUnavailable",
    "identity_store_for",
    "link_with_password",
    "link_with_token",
    "load_linked_identity",
    "member_id_for",
    "normalize_identity_mode",
    "resolve_backend_identity",
    "unlink",
]
