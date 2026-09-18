"""Origin comparison for the cross-site guard on credential-mutating routes.

The guard exists because CORS cannot cover this: a cross-site JSON POST is
executed by the server even when the browser then refuses to hand back the
response, and KAGWeb's own CORS is permissive whenever auth is off (its
default). So the decision is made from the request's own ``Origin``.
"""

from __future__ import annotations

import pytest

from kagweb.services.config.origins import (
    normalize_origins,
    origin_is_trusted,
    origin_netloc,
    request_authority,
)


@pytest.mark.parametrize(
    "origin,expected",
    [
        ("https://app.example", "app.example"),
        ("https://app.example/", "app.example"),
        ("https://app.example:443", "app.example"),  # default port dropped
        ("http://app.example:80", "app.example"),
        ("http://app.example:8080", "app.example:8080"),
        ("https://APP.EXAMPLE", "app.example"),  # case folded
        ("http://user:pw@app.example", "app.example"),  # userinfo ignored
        ("app.example", "app.example"),  # bare Host header form
        ("app.example:9000", "app.example:9000"),
        ("https://app.example/path?x=1#f", "app.example"),  # path is not identity
        ("host:abc", ""),  # malformed port
        ("", ""),
    ],
)
def test_origin_netloc(origin: str, expected: str) -> None:
    assert origin_netloc(origin) == expected


def test_same_host_is_trusted_regardless_of_scheme() -> None:
    # TLS is normally terminated in front of the app, so the browser's https://
    # and the forwarded Host header can legitimately disagree on scheme.
    assert origin_is_trusted("https://app.example", "app.example", [])
    assert origin_is_trusted("http://app.example", "app.example", [])


@pytest.mark.parametrize(
    "origin,host",
    [
        ("https://evil.example", "app.example"),
        ("https://app.example.evil.test", "app.example"),  # suffix is not a match
        ("http://app.example:9999", "app.example:8080"),  # different port
    ],
)
def test_a_different_host_is_not_trusted(origin: str, host: str) -> None:
    assert origin_is_trusted(origin, host, []) is False


def test_null_origin_is_never_trusted() -> None:
    """A sandboxed iframe or file:// page sends ``Origin: null``; that is the
    standard way to launder a cross-site request through an opaque origin."""
    assert origin_is_trusted("null", "app.example", []) is False
    assert origin_is_trusted("NULL", "app.example", []) is False


def test_absent_origin_is_allowed() -> None:
    """curl, the SDK and the test client send no Origin — not a browser form."""
    assert origin_is_trusted("", "app.example", []) is True
    assert origin_is_trusted(None, "app.example", []) is True


def test_an_operator_configured_frontend_origin_is_trusted() -> None:
    """A deployment serving the UI from elsewhere names it in CORS_ORIGIN(S)."""
    allowed = normalize_origins("https://ui.example, https://alt.example")
    assert origin_is_trusted("https://ui.example", "api.internal", allowed) is True
    assert origin_is_trusted("https://alt.example", "api.internal", allowed) is True
    assert origin_is_trusted("https://evil.example", "api.internal", allowed) is False


def test_a_wildcard_allowlist_is_not_an_exemption() -> None:
    """``*`` means "any site", which is precisely the condition being defended
    against — it must not be read as "origins are unrestricted, so allow"."""
    assert origin_is_trusted("https://evil.example", "app.example", ["*"]) is False


def test_request_authority_prefers_the_forwarded_host() -> None:
    """The frontend's /api rewrite (and any reverse proxy) rewrites Host to the
    backend port while appending X-Forwarded-Host with the browser's original
    authority — the same-origin comparison must use the latter."""
    assert request_authority("127.0.0.1:8082", "127.0.0.1:8092") == "127.0.0.1:8092"
    # No forwarded header: the direct-request form keeps the plain Host.
    assert request_authority("127.0.0.1:8082", "") == "127.0.0.1:8082"
    assert request_authority("127.0.0.1:8082", None) == "127.0.0.1:8082"
    assert request_authority("127.0.0.1:8082", "   ") == "127.0.0.1:8082"


def test_same_origin_behind_the_frontend_proxy() -> None:
    """Live-verified proxy shape (M2.4 review F-8): Origin names the frontend
    port, Host names the backend port, X-Forwarded-Host names the frontend."""
    authority = request_authority("127.0.0.1:8082", "127.0.0.1:8092")
    assert origin_is_trusted("http://127.0.0.1:8092", authority, []) is True
    # An attacking site is still refused, whichever host form is used.
    assert origin_is_trusted("https://evil.example", authority, []) is False
    # And the pre-fix behavior — comparing against the raw Host — must fail,
    # which is why the guard call sites route through request_authority.
    assert origin_is_trusted("http://127.0.0.1:8092", "127.0.0.1:8082", []) is False
