"""Profile-family derivation from the preset registry.

The stored profile dict carries a ``preset`` name; the transport family
lives on the preset entry. Every consumer (materialization gate, turn
access gate) must derive it through :func:`profile_family` — reading a
``family`` key off the profile dict always yields empty.
"""

from __future__ import annotations

from kagweb.services.agent_loop.settings import profile_family


def test_cli_and_http_presets_derive_their_families() -> None:
    assert profile_family({"preset": "claude-code"}) == "cli"
    assert profile_family({"preset": "codex"}) == "cli"
    assert profile_family({"preset": "opencode"}) == "cli"
    assert profile_family({"preset": "intellect"}) == "cli"
    assert profile_family({"preset": "intellect-team"}) == "http"
    assert profile_family({"preset": "hermes"}) == "http"
    assert profile_family({"preset": "custom-http"}) == "http"


def test_empty_and_unknown_inputs_degrade_to_empty() -> None:
    assert profile_family(None) == ""
    assert profile_family({}) == ""
    assert profile_family({"preset": ""}) == ""
    assert profile_family({"preset": "no-such-preset"}) == ""
