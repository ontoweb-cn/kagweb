from __future__ import annotations

import re
from typing import Any, Iterable
from urllib.parse import urlparse

_ORIGIN_SEPARATORS = re.compile(r"[,;\n]+")


def _raw_origin_items(value: Any) -> Iterable[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        items: list[str] = []
        for item in value:
            items.extend(_raw_origin_items(item))
        return items
    return _ORIGIN_SEPARATORS.split(str(value))


def normalize_origin(value: Any) -> str:
    """Normalize a browser Origin value for CORS allowlists.

    Operators often paste values as ``host:port`` or separate multiple origins
    with semicolons. Browsers always send an Origin as ``scheme://host[:port]``.
    This helper makes common deployment input tolerant while keeping the output
    as exact origins for Starlette's CORSMiddleware.
    """

    origin = str(value or "").strip().rstrip("/")
    if not origin:
        return ""
    if origin in {"*", "null"}:
        return origin
    if "://" not in origin:
        origin = f"http://{origin}"

    try:
        parsed = urlparse(origin)
    except ValueError:
        return origin
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}".rstrip("/")
    return origin


def normalize_origins(value: Any) -> list[str]:
    origins: list[str] = []
    seen: set[str] = set()
    for raw in _raw_origin_items(value):
        origin = normalize_origin(raw)
        if origin and origin not in seen:
            origins.append(origin)
            seen.add(origin)
    return origins


def origin_netloc(origin: Any) -> str:
    """The ``host[:port]`` of a browser Origin or a ``Host`` header.

    Compared without the scheme because TLS is commonly terminated in front of
    the app: the browser's ``https://host`` and the ``Host`` header a proxy
    forwards can legitimately disagree on scheme while naming the same host.
    """
    raw = str(origin or "").strip().rstrip("/")
    if not raw:
        return ""
    # Strip a scheme and any userinfo/path/query/fragment, then parse the
    # authority directly. A bare ``host[:port]`` (what the ``Host`` header
    # carries) is not a valid URL, and urlparse reports no hostname for it.
    if "//" in raw:
        raw = raw.split("//", 1)[1]
    raw = raw.split("@")[-1]
    authority = raw.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
    if not authority:
        return ""
    host, separator, port = authority.partition(":")
    host = host.strip().lower()
    if not host:
        return ""
    if not separator:
        return host
    if not port.isdigit():
        return ""
    number = int(port)
    if number == 80 or number == 443:
        return host
    return f"{host}:{number}"


def origin_is_trusted(
    origin: Any,
    host: Any,
    allowed_origins: Iterable[str] | None = None,
) -> bool:
    """Whether a browser ``Origin`` may drive a credential-mutating request.

    CORS cannot be the guard here. A cross-site JSON POST is *executed* by the
    server even when the browser then refuses to hand the response back — the
    side effect is the attack — and KAGWeb's own CORS is permissive whenever
    auth is off (its default), which lets the preflight through and the request
    actually fly. So this checks the request's own origin instead, independent
    of CORS:

    * **No ``Origin``** — not a browser form/fetch (curl, the SDK, tests).
      Nothing to authenticate, so it is allowed; those callers were already
      free of this concern.
    * **``null``** — a sandboxed iframe or ``file://`` page. Never trusted: it
      is the classic way to launder a cross-site request through an opaque
      origin.
    * **Same host as the request** — the normal same-origin app. Scheme is
      ignored (see :func:`origin_netloc`).
    * **An explicitly configured CORS origin** — an operator who deliberately
      serves the frontend from elsewhere keeps working. A configured ``*`` is
      deliberately *not* an exemption: it means "any site", which is the
      condition being defended against.
    """
    raw = str(origin or "").strip()
    if not raw:
        return True
    if raw.lower() == "null":
        return False

    request_host = str(host or "").strip().lower()
    candidate = origin_netloc(raw)
    if candidate and request_host and candidate == origin_netloc(f"//{request_host}"):
        return True

    explicit = {
        normalize_origin(item)
        for item in (allowed_origins or [])
        if normalize_origin(item) not in {"", "*"}
    }
    return raw.rstrip("/") in explicit
