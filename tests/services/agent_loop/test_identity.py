"""The identity bridge: who a turn runs as on a remote agent service.

Two bridges with genuinely different security properties, so most of these
assert *which* one was applied and which credential ended up in the request —
not merely that something was.
"""

from __future__ import annotations

from dataclasses import replace
import json
import time

import httpx
import pytest

from kagweb.services.agent_loop import build_agent_loop_backend
from kagweb.services.agent_loop.identity import (
    BackendIdentity,
    IdentityUnavailable,
    identity_store_for,
    member_id_for,
    normalize_identity_mode,
    resolve_backend_identity,
)
from kagweb.services.agent_loop.identity_store import IntellectIdentityStore, LinkedIdentity
from kagweb.services.agent_loop.protocol import AgentLoopRequest

# Only the wire-level tests are async; the rest are pure resolution logic, so
# there is no module-wide asyncio mark.


@pytest.fixture
def user_root(tmp_path, monkeypatch):
    """Point the per-owner secrets root at a temp dir for this test."""
    import kagweb.multi_user.paths as paths

    root = tmp_path / "system"
    monkeypatch.setattr(paths, "SYSTEM_ROOT", root)
    return root


# ── the member id the service will accept ──────────────────────────────────


def test_member_id_is_formatted_for_the_service() -> None:
    """KAGWeb ids are ``u_<hex>``; the service accepts ``mem_`` + an
    alphanumeric-ish body of at most 60 chars."""
    assert member_id_for("u_ab12cd") == "mem_u_ab12cd"
    # Anything outside the accepted alphabet is folded, not dropped: refusing
    # to send it would make attribution fail confusingly for imported users.
    # Folding appends a digest (see the collision test below), so assert the
    # contract rather than one exact spelling.
    folded = member_id_for("a b/c")
    assert folded.startswith("mem_a-b-c")
    assert member_id_for("") == ""


def test_an_unknown_mode_cannot_become_the_permissive_one() -> None:
    """A typo must not silently enable delegation, and must not become `off`'s
    opposite — unknown means send nothing."""
    assert normalize_identity_mode("token") == "token"
    assert normalize_identity_mode("TOKEN_REQUIRED") == "token_required"
    assert normalize_identity_mode("tokn") == "off"
    assert normalize_identity_mode(None) == "off"


# ── mode `off` is the pre-existing behaviour ───────────────────────────────


def test_off_mode_sends_exactly_the_configured_profile(user_root) -> None:
    identity = resolve_backend_identity(
        {"api_key": "svc", "headers": {"X-Custom": "1"}, "identity_mode": "off"},
        user_id="u_1",
    )
    assert identity is not None
    assert identity.api_key == "svc"
    assert identity.headers == {"X-Custom": "1"}
    # No namespace: a deployment that never opted in must not see its remote
    # session ids change.
    assert identity.session_prefix == ""
    assert identity.tier == "off"


# ── mode `header`: attribution only ────────────────────────────────────────


def test_header_mode_attributes_the_turn_to_the_account(user_root) -> None:
    identity = resolve_backend_identity(
        {"api_key": "svc", "identity_mode": "header"}, user_id="u_abc"
    )
    assert identity is not None
    assert identity.tier == "header"
    assert identity.headers["X-Intellect-User"] == "mem_u_abc"
    # Attribution keeps the service credential: it is not an access boundary.
    assert identity.api_key == "svc"
    assert identity.session_prefix == "u_abc:"


def test_attribution_uses_the_linked_member_id_when_there_is_one(user_root) -> None:
    """A connected account is what the turn is attributed to.

    Attribution otherwise derives a member id from KAGWeb's own account id,
    which names an account the service has never seen. Once a user has signed
    in to the service, that sign-in is the identity worth recording — it is the
    same shape the sibling enterprise UI presents (service key authenticates,
    the real member id attributes), so a service-side audit sees one user, not
    two different spellings of them.
    """
    identity_store_for("u_1").save(_link("u_1"))
    identity = resolve_backend_identity(
        {"api_key": "svc", "identity_mode": "header", "url": _GW}, user_id="u_1"
    )
    assert identity is not None
    assert identity.tier == "header"
    assert identity.headers["X-Intellect-User"] == "mem_u_1"
    # Still attribution: the deployment key stays the credential.
    assert identity.api_key == "svc"


def test_attribution_ignores_a_link_made_to_another_service(user_root) -> None:
    """An id minted by a different instance names an account that does not
    exist there, so the derived id is the only one worth sending."""
    foreign = replace(
        _link("u_1", service_origin="https://other.test"), member_id="mem_from_elsewhere"
    )
    identity_store_for("u_1").save(foreign)
    identity = resolve_backend_identity(
        {"api_key": "svc", "identity_mode": "header", "url": _GW}, user_id="u_1"
    )
    assert identity is not None
    assert identity.headers["X-Intellect-User"] == member_id_for("u_1")
    assert identity.headers["X-Intellect-User"] != "mem_from_elsewhere"


def test_attribution_ignores_an_expired_link(user_root) -> None:
    identity_store_for("u_1").save(_link("u_1", expires_at=1.0))
    identity = resolve_backend_identity(
        {"api_key": "svc", "identity_mode": "header", "url": _GW}, user_id="u_1"
    )
    assert identity is not None
    assert identity.headers["X-Intellect-User"] == member_id_for("u_1")


def test_a_profile_cannot_forge_the_identity_header(user_root) -> None:
    """The header is the service's reason to trust the account, so a value from
    the profile's free-form headers must not survive next to the resolved one —
    in any casing."""
    identity = resolve_backend_identity(
        {
            "api_key": "svc",
            "identity_mode": "header",
            "headers": {"x-intellect-user": "mem_someone_else", "X-Keep": "yes"},
        },
        user_id="u_real",
    )
    assert identity is not None
    (user_header,) = [k for k in identity.headers if k.lower() == "x-intellect-user"]
    assert identity.headers[user_header] == "mem_u_real"
    assert identity.headers["X-Keep"] == "yes"


# ── mode `token`: delegation ───────────────────────────────────────────────


#: The service these unit tests imagine the deployment talks to. A link records
#: the origin its token belongs to, and resolution only presents that token back
#: to that origin, so the two must agree.
_GW = "https://gw.test"


def _link(
    user_id: str,
    *,
    token: str = "imt_abc",
    expires_at: float = 0.0,
    service_origin: str = _GW,
) -> LinkedIdentity:
    return LinkedIdentity(
        kagweb_user_id=user_id,
        member_id="mem_u_1",
        token=token,
        team_id="team_1",
        project_id="proj_1",
        service_origin=service_origin,
        expires_at=expires_at,
    )


def _profile(**overrides) -> dict:
    """A token-mode profile pointing at the test service."""
    return {"api_key": "svc", "identity_mode": "token", "url": _GW, **overrides}


def test_token_mode_presents_the_users_own_credential(user_root) -> None:
    identity_store_for("u_1").save(_link("u_1"))
    identity = resolve_backend_identity(_profile(), user_id="u_1")
    assert identity is not None
    assert identity.tier == "member"
    assert identity.api_key == "imt_abc"
    assert identity.session_prefix == "u_1:"
    # Member mode ignores the user header (the token decides), so sending one
    # would only mislead; team/project come from the stored link, never a client.
    assert "X-Intellect-User" not in identity.headers
    assert identity.headers["X-Intellect-Team"] == "team_1"
    assert identity.headers["X-Intellect-Project"] == "proj_1"


def test_an_unlinked_user_under_token_mode_falls_back_to_attribution(user_root) -> None:
    """Never linked is a genuine step up from sending nothing, and the
    deployment asked for delegation only where available."""
    identity = resolve_backend_identity(_profile(), user_id="u_new")
    assert identity is not None
    assert identity.tier == "header"
    assert identity.degraded is True
    assert identity.api_key == "svc"


def test_token_required_refuses_an_unlinked_user(user_root) -> None:
    with pytest.raises(IdentityUnavailable):
        resolve_backend_identity(
            {"api_key": "svc", "identity_mode": "token_required"}, user_id="u_new"
        )


def test_an_expired_link_fails_instead_of_escalating(user_root) -> None:
    """The security-critical case. Falling back to the service key would grant
    *more* reach than the token that just lapsed — an escalation delivered
    exactly when the restriction started to matter."""
    identity_store_for("u_1").save(_link("u_1", expires_at=time.time() - 10))
    with pytest.raises(IdentityUnavailable) as exc:
        resolve_backend_identity(_profile(), user_id="u_1")
    # The message names the linked member so the user knows which account to
    # reconnect; it is not a bare "not authorized".
    assert "mem_u_1" in str(exc.value)


def test_a_link_that_names_another_account_is_not_used(user_root) -> None:
    """A copied or restored file must not lend one account's identity to
    another."""
    identity_store_for("u_1").save(_link("u_someone_else"))
    identity = resolve_backend_identity(_profile(), user_id="u_1")
    assert identity is not None
    assert identity.api_key == "svc"  # fell back, did not adopt the foreign link
    assert identity.degraded is True


def test_a_corrupt_credential_file_reads_as_unlinked(user_root) -> None:
    """`not linked` is the caller's safe branch; an unreadable leftover must not
    turn into a failed turn."""
    store = identity_store_for("u_1")
    store.root.mkdir(parents=True, exist_ok=True)
    store.credentials_path.write_text("{ not json", encoding="utf-8")
    assert store.load("u_1") is None


def test_the_stored_credential_is_owner_only(user_root) -> None:
    store = identity_store_for("u_1")
    store.save(_link("u_1"))
    payload = json.loads(store.credentials_path.read_text(encoding="utf-8"))
    assert payload["member_id"] == "mem_u_1"
    mode = store.credentials_path.stat().st_mode & 0o777
    assert mode == 0o600, oct(mode)
    assert store.clear() is True
    assert store.clear() is False


def test_a_non_http_family_gets_no_identity() -> None:
    """A local CLI subprocess has no way to present a bearer token or headers."""
    assert resolve_backend_identity({"identity_mode": "token"}, family="cli") is None


# ── end to end through the factory and a request ───────────────────────────


@pytest.mark.asyncio
async def test_the_resolved_identity_reaches_the_wire(user_root) -> None:
    """The factory resolves deployment config; the turn's identity is applied
    afterwards and must actually replace the credential and the header set."""
    identity_store_for("u_1").save(_link("u_1", token="imt_user"))
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/events"):
            return httpx.Response(
                200,
                text='data: {"event": "run.completed", "output": "ok"}\n\n: stream closed\n',
                headers={"content-type": "text/event-stream"},
            )
        return httpx.Response(202, json={"run_id": "r1", "status": "started"})

    backend = build_agent_loop_backend(
        {
            "preset": "intellect-team",
            "url": _GW,
            "api_key": "svc",
            "identity_mode": "token",
        }
    )
    backend._transport = httpx.MockTransport(handler)
    identity = resolve_backend_identity(_profile(), user_id="u_1")
    backend.with_identity(identity)

    [event async for event in backend.run(AgentLoopRequest(prompt="hi", session_id="s1"))]

    start = next(call for call in seen if call.url.path.endswith("/v1/runs"))
    assert start.headers["Authorization"] == "Bearer imt_user"
    body = json.loads(start.content)
    # The remote session is scoped to the account, so two users cannot collide.
    assert body["session_id"] == "u_1:s1"


@pytest.mark.asyncio
async def test_off_mode_leaves_the_session_id_untouched(user_root) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/events"):
            return httpx.Response(
                200,
                text='data: {"event": "run.completed", "output": "ok"}\n\n: stream closed\n',
                headers={"content-type": "text/event-stream"},
            )
        return httpx.Response(202, json={"run_id": "r1", "status": "started"})

    backend = build_agent_loop_backend({"preset": "intellect-team", "url": _GW, "api_key": "svc"})
    backend._transport = httpx.MockTransport(handler)
    [event async for event in backend.run(AgentLoopRequest(prompt="hi", session_id="s1"))]

    start = next(call for call in seen if call.url.path.endswith("/v1/runs"))
    assert json.loads(start.content)["session_id"] == "s1"
    assert start.headers["Authorization"] == "Bearer svc"


def test_the_base_backend_ignores_identity_by_default(user_root) -> None:
    """A backend that never opted into the hook keeps its own credential."""
    from kagweb.capabilities.chat.capability import ChatCapability

    class _Duck:
        name = "duck"

    # No with_identity on the object at all: the capability must not care.
    ChatCapability._apply_identity(_Duck(), {"identity_mode": "header"}, "en")


def test_store_round_trips_only_the_declared_fields(user_root) -> None:
    store: IntellectIdentityStore = identity_store_for("u_9")
    store.save(_link("u_9"))
    record = store.load("u_9")
    assert record is not None
    assert record.kagweb_user_id == "u_9"
    assert record.token == "imt_abc"
    # Never a raw exception on an unsupported future schema.
    store.credentials_path.write_text(
        json.dumps({"version": 99, "member_id": "m", "token": "t"}), encoding="utf-8"
    )
    assert store.load("u_9") is None


# ── linking: verified against the service before anything is stored ────────


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None) -> None:
        self.status_code = status_code
        self._payload = payload or {}
        self.content = b"x" if payload is not None else b""

    def json(self) -> dict:
        return self._payload


class _FakeClient:
    """Stand-in for httpx.AsyncClient, routed by path."""

    def __init__(self, responses: dict[str, _FakeResponse]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, str, dict]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc) -> None:
        return None

    async def get(self, url: str, headers: dict | None = None):
        self.calls.append(("GET", url, headers or {}))
        return self.responses.get("GET", _FakeResponse(404))

    async def post(self, url: str, json: dict | None = None, headers: dict | None = None):
        self.calls.append(("POST", url, {**(headers or {}), "json": json or {}}))
        return self.responses.get("POST", _FakeResponse(404))


@pytest.fixture
def gateway(monkeypatch):
    """Point the link flow at a fake service with a configured gateway URL."""
    import httpx

    import kagweb.services.agent_loop.identity as identity
    import kagweb.services.agent_loop.settings as loop_settings

    monkeypatch.setattr(
        loop_settings,
        "get_agent_loop_settings",
        lambda: {"profiles": [{"id": "p", "preset": "intellect-team", "url": _GW}]},
    )
    monkeypatch.setattr(
        loop_settings, "resolve_primary_profile", lambda block=None: block["profiles"][0]
    )

    holder: dict[str, _FakeClient] = {}

    def _client(*args, **kwargs):
        return holder["client"]

    monkeypatch.setattr(httpx, "AsyncClient", _client)
    return holder


@pytest.mark.asyncio
async def test_a_pasted_token_is_verified_before_it_is_stored(gateway) -> None:
    """Verifying at link time turns a wrong token into an immediate, specific
    message instead of a failed conversation much later."""
    from kagweb.services.agent_loop.identity import IdentityLinkError, link_with_token

    gateway["client"] = _FakeClient({"GET": _FakeResponse(401)})
    with pytest.raises(IdentityLinkError) as exc:
        await link_with_token("imt_bad", user_id="u_1")
    assert "not accepted" in str(exc.value).lower()

    gateway["client"] = _FakeClient(
        {"GET": _FakeResponse(200, {"id": "mem_u_1", "display_name": "A"})}
    )
    record = await link_with_token("imt_good", user_id="u_1")
    assert record.member_id == "mem_u_1"
    assert record.token == "imt_good"
    # Bound to the account that made the request.
    assert record.kagweb_user_id == "u_1"
    # The identity check carries the token, not the service key.
    _method, url, headers = gateway["client"].calls[0]
    assert headers["Authorization"] == "Bearer imt_good"
    assert url.endswith("/api/members/me")


@pytest.mark.asyncio
async def test_a_password_login_stores_only_the_minted_token(gateway) -> None:
    from kagweb.services.agent_loop.identity import link_with_password

    gateway["client"] = _FakeClient(
        {"POST": _FakeResponse(200, {"token": "imt_minted", "member": {"id": "mem_u_2"}})}
    )
    record = await link_with_password("alice", "hunter2", user_id="u_2")
    assert record.token == "imt_minted"
    assert record.member_id == "mem_u_2"
    assert record.kagweb_user_id == "u_2"
    # The password went in the request body and nowhere near the record.
    _method, _url, headers = gateway["client"].calls[0]
    assert headers["json"] == {"login_name": "alice", "password": "hunter2"}
    assert "hunter2" not in record.to_payload().values()


@pytest.mark.asyncio
async def test_a_rejected_login_reports_the_right_reason(gateway) -> None:
    from kagweb.services.agent_loop.identity import IdentityLinkError, link_with_password

    gateway["client"] = _FakeClient({"POST": _FakeResponse(401)})
    with pytest.raises(IdentityLinkError) as bad:
        await link_with_password("alice", "wrong", user_id="u_2")
    assert "not accepted" in str(bad.value).lower()

    gateway["client"] = _FakeClient({"POST": _FakeResponse(423)})
    with pytest.raises(IdentityLinkError) as locked:
        await link_with_password("alice", "wrong", user_id="u_2")
    assert "locked" in str(locked.value).lower()


@pytest.mark.asyncio
async def test_unlinking_removes_the_link_even_if_revocation_fails(gateway) -> None:
    """A user must be able to disconnect while the service is unreachable —
    otherwise a broken service would trap them in a link they cannot drop."""
    import httpx

    from kagweb.services.agent_loop.identity import identity_store_for, unlink

    identity_store_for("u_1").save(_link("u_1"))
    gateway["client"] = _FakeClient({})

    async def _boom(*args, **kwargs):
        raise httpx.ConnectError("unreachable")

    gateway["client"].post = _boom  # type: ignore[method-assign]
    assert await unlink("u_1") is True
    assert identity_store_for("u_1").load("u_1") is None


@pytest.mark.asyncio
async def test_the_link_never_targets_a_user_supplied_host(gateway, monkeypatch) -> None:
    """The service URL comes from the deployment's profile, so the link form
    cannot be turned into an outbound request to an arbitrary host."""
    from kagweb.services.agent_loop.identity import IdentityLinkError, link_with_token
    import kagweb.services.agent_loop.settings as loop_settings

    gateway["client"] = _FakeClient({"GET": _FakeResponse(200, {"id": "mem_x"})})
    # A deployment with no service configured: the profile carries no URL, and
    # there is nowhere legitimate to send the credential.
    monkeypatch.setattr(
        loop_settings, "resolve_primary_profile", lambda block=None: {"preset": "intellect-team"}
    )

    with pytest.raises(IdentityLinkError) as exc:
        await link_with_token("imt_x", user_id="u_1")
    assert "no agent service" in str(exc.value).lower()
    # Nothing was attempted: the failure is decided before any request is built.
    assert gateway["client"].calls == []


@pytest.mark.asyncio
async def test_an_explicitly_applied_identity_is_not_overwritten_at_run(user_root) -> None:
    """The capability resolves the identity (it has the turn's language) and the
    backend must use exactly that when the turn starts — not re-resolve and
    replace it, which would silently drop the user's token."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/events"):
            return httpx.Response(
                200,
                text='data: {"event": "run.completed", "output": "ok"}\n\n: stream closed\n',
                headers={"content-type": "text/event-stream"},
            )
        return httpx.Response(202, json={"run_id": "r1"})

    backend = build_agent_loop_backend(
        {"preset": "intellect-team", "url": _GW, "api_key": "svc", "identity_mode": "token"}
    )
    backend._transport = httpx.MockTransport(handler)
    backend.with_identity(
        BackendIdentity(
            api_key="imt_explicit",
            headers={"X-Intellect-Team": "t1"},
            session_prefix="u_1:",
            tier="member",
        )
    )

    [event async for event in backend.run(AgentLoopRequest(prompt="hi", session_id="s1"))]
    start = next(call for call in seen if call.url.path.endswith("/v1/runs"))
    assert start.headers["Authorization"] == "Bearer imt_explicit"
    assert json.loads(start.content)["session_id"] == "u_1:s1"


@pytest.mark.asyncio
async def test_a_direct_caller_still_gets_the_profile_identity(user_root) -> None:
    """Nothing applied an identity (an embedding calling run directly), so a
    backend configured for token mode must resolve one itself rather than
    silently run as the deployment."""
    identity_store_for("u_1").save(_link("u_1", token="imt_linked"))
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/events"):
            return httpx.Response(
                200,
                text='data: {"event": "run.completed", "output": "ok"}\n\n: stream closed\n',
                headers={"content-type": "text/event-stream"},
            )
        return httpx.Response(202, json={"run_id": "r1"})

    backend = build_agent_loop_backend(
        {"preset": "intellect-team", "url": _GW, "api_key": "svc", "identity_mode": "token"}
    )
    backend._transport = httpx.MockTransport(handler)

    from kagweb.multi_user.context import reset_current_user, set_current_user
    from kagweb.multi_user.models import CurrentUser, UserScope

    user = CurrentUser(
        id="u_1",
        username="u_1",
        role="user",
        scope=UserScope(kind="user", user_id="u_1", root=user_root),
    )
    token = set_current_user(user)
    try:
        [event async for event in backend.run(AgentLoopRequest(prompt="hi", session_id="s1"))]
    finally:
        reset_current_user(token)

    start = next(call for call in seen if call.url.path.endswith("/v1/runs"))
    assert start.headers["Authorization"] == "Bearer imt_linked"


# ── the token is bound to the service it was minted against ────────────────


def test_service_origin_normalizes_without_over_matching() -> None:
    """Path and default port are not part of the identity; scheme and a
    non-default port are."""
    from kagweb.services.agent_loop.identity import service_origin

    assert service_origin("https://gw.example") == "https://gw.example"
    assert service_origin("https://GW.example/v1/runs") == "https://gw.example"
    assert service_origin("https://gw.example:443") == "https://gw.example"
    assert service_origin("http://gw.example:80/x") == "http://gw.example"
    assert service_origin("http://gw.example:8642") == "http://gw.example:8642"
    assert service_origin("") == ""
    assert service_origin("not a url") == ""


def test_a_retargeted_service_never_receives_the_linked_token(user_root) -> None:
    """The profile URL is admin-configurable, so a linked member token must not
    follow it: otherwise repointing the profile (or selecting a different
    primary) ships every linked user's credential to the new host."""
    identity_store_for("u_1").save(
        LinkedIdentity(
            kagweb_user_id="u_1",
            member_id="mem_u_1",
            token="imt_SECRET",
            service_origin="https://intellect.corp.example",
        )
    )
    profile = {"api_key": "svc", "identity_mode": "token"}

    # The service it was minted against — any path on it — is fine.
    for url in ("https://intellect.corp.example", "https://intellect.corp.example/v1/runs"):
        identity = resolve_backend_identity({**profile, "url": url}, user_id="u_1")
        assert identity is not None
        assert identity.api_key == "imt_SECRET"

    # Anything else is refused, and refused without downgrading to the key.
    for url in (
        "https://attacker.example",
        "http://intellect.corp.example",  # scheme downgrade
        "https://intellect.corp.example:8443",  # different port
    ):
        with pytest.raises(IdentityUnavailable):
            resolve_backend_identity({**profile, "url": url}, user_id="u_1")


def test_a_link_without_a_recorded_origin_is_not_used(user_root) -> None:
    """A link written by an earlier build has no binding, and there is no safe
    way to guess which service its token belongs to — so it fails closed and
    the user reconnects once."""
    identity_store_for("u_1").save(
        LinkedIdentity(kagweb_user_id="u_1", member_id="mem_u_1", token="imt_LEGACY")
    )
    with pytest.raises(IdentityUnavailable):
        resolve_backend_identity(
            {"api_key": "svc", "identity_mode": "token", "url": "https://gw.example"},
            user_id="u_1",
        )


def test_the_origin_binding_survives_a_store_round_trip(user_root) -> None:
    store = identity_store_for("u_1")
    store.save(
        LinkedIdentity(
            kagweb_user_id="u_1",
            member_id="mem_u_1",
            token="t",
            service_origin="https://gw.example:8443",
        )
    )
    record = store.load("u_1")
    assert record is not None
    assert record.service_origin == "https://gw.example:8443"


@pytest.mark.asyncio
async def test_a_non_service_response_is_named_not_crashed(gateway) -> None:
    """An ingress page or login portal answers 200 with HTML. Raising a parser
    error would surface as a 500 with nothing to act on."""
    from kagweb.services.agent_loop.identity import IdentityLinkError, link_with_token

    gateway["client"] = _FakeClient({"GET": _FakeResponse(200, None)})
    gateway["client"].responses["GET"].content = b"<html>portal</html>"
    gateway["client"].responses["GET"].json = lambda: (_ for _ in ()).throw(ValueError("no json"))

    with pytest.raises(IdentityLinkError) as exc:
        await link_with_token("imt_x", user_id="u_1")
    assert "not the agent service" in str(exc.value)


@pytest.mark.asyncio
async def test_a_plaintext_remote_service_refuses_the_password(gateway, monkeypatch) -> None:
    """A password is the user's own reusable credential; putting it on the wire
    in the clear is refused even if the deployment sends its own key that way."""
    import kagweb.services.agent_loop.identity as identity
    from kagweb.services.agent_loop.identity import IdentityLinkError, link_with_password

    monkeypatch.setattr(identity, "_gateway_url", _async_url("http://gw.example:8642"))
    with pytest.raises(IdentityLinkError) as exc:
        await link_with_password("alice", "hunter2", user_id="u_1")
    assert "HTTPS" in str(exc.value)

    # Loopback is not a network: the same call is allowed there.
    monkeypatch.setattr(identity, "_gateway_url", _async_url("http://127.0.0.1:8642"))
    gateway["client"] = _FakeClient(
        {"POST": _FakeResponse(200, {"token": "imt_ok", "member": {"id": "mem_u_1"}})}
    )
    record = await link_with_password("alice", "hunter2", user_id="u_1")
    assert record.token == "imt_ok"


def _async_url(value: str):
    async def _get() -> str:
        return value

    return _get


def test_member_id_folding_cannot_collide_two_accounts() -> None:
    """Sanitizing is not injective on its own: ``a/b`` and ``a-b`` both fold to
    ``a-b``, which would attribute two KAGWeb accounts to one service principal.
    An id that had to be folded therefore carries a digest of the original."""
    from kagweb.services.agent_loop.identity import member_id_for

    assert member_id_for("a/b") != member_id_for("a-b")
    assert member_id_for("a b") != member_id_for("a-b")
    assert member_id_for("a.b") != member_id_for("a-b")


def test_member_id_leaves_ordinary_ids_alone() -> None:
    """The digest is per-id, so a normal account keeps a clean, readable id."""
    from kagweb.services.agent_loop.identity import member_id_for

    assert member_id_for("u_ab12cd") == "mem_u_ab12cd"
    assert member_id_for("local-admin") == "mem_local-admin"


def test_member_id_always_satisfies_the_service_contract() -> None:
    """``mem_`` + ``[A-Za-z0-9_-]{1,60}``, total length 5..64 — anything else is
    silently dropped by the service, which would look like attribution failing."""
    from kagweb.services.agent_loop.identity import member_id_for

    for raw in ("u_" + "f" * 40, "u_" + "f" * 200, "x" * 500, "a" * 40 + "/b", "한글/이름"):
        value = member_id_for(raw)
        assert 5 <= len(value) <= 64, (raw, value)
        assert value.startswith("mem_")
        body = value[4:]
        assert body and all(c.isascii() and (c.isalnum() or c in "_-") for c in body), value
    assert member_id_for("") == ""


# ── the instance tenant: a deployment fact, sent on every request ──────────


def test_the_tenant_header_rides_every_request() -> None:
    """The tenant is deployment configuration, not per-turn identity.

    Intellect validates it against the instance it is configured with (and
    answers 400/403 on a mismatch), so it has to be on the request that starts
    a run *and* on the follow-ups the turn makes. The two implementations read
    different header names and each ignores the other's, so both are sent.
    """
    backend = build_agent_loop_backend(
        {
            "preset": "intellect-team",
            "url": _GW,
            "api_key": "svc",
            "tenant_id": "ab" * 16,
        }
    )
    assert backend is not None
    headers = backend._headers()
    assert headers["X-Tenant-Id"] == "ab" * 16
    assert headers["X-Intellect-Tenant-Id"] == "ab" * 16


def test_no_tenant_configured_sends_no_tenant_header() -> None:
    """Empty means "the service's own default" — not an empty header, which the
    service would reject as malformed."""
    backend = build_agent_loop_backend({"preset": "intellect-team", "url": _GW, "api_key": "svc"})
    assert backend is not None
    headers = backend._headers()
    assert "X-Tenant-Id" not in headers
    assert "X-Intellect-Tenant-Id" not in headers


def test_the_tenant_survives_applying_a_turn_identity() -> None:
    """``with_identity`` replaces the profile's headers wholesale, so the tenant
    must not be stored there — a turn that applied identity would otherwise
    drop it and fail on the service's tenant check."""
    backend = build_agent_loop_backend(
        {"preset": "intellect-team", "url": _GW, "api_key": "svc", "tenant_id": "cd" * 16}
    )
    assert backend is not None
    backend.with_identity(
        BackendIdentity(
            api_key="svc",
            headers={"X-Intellect-User": "mem_u_1"},
            session_prefix="u_1:",
            tier="header",
            member_id="mem_u_1",
        )
    )
    headers = backend._headers()
    assert headers["X-Tenant-Id"] == "cd" * 16
    assert headers["X-Intellect-User"] == "mem_u_1"
