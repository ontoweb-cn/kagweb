"""The per-turn access gate, and how it resolves what a turn needs.

Regression cover for `backend-llm-deployment.md` P0-1: the gate used to demand
an *LLM* grant for every non-admin turn, even when the chat capability was
delegating to an agent backend that brings its own model. In a pure agent-loop
deployment that rejected every non-admin turn, so the deployment served admins
only — and nothing in the suite noticed, because the gate had no tests at all.
"""

from __future__ import annotations

from typing import Any

import pytest

from kagweb.multi_user import model_access
from kagweb.multi_user.context import reset_current_user, set_current_user
from kagweb.multi_user.models import CurrentUser, UserScope
from kagweb.services.session.turns.request_preparer import _effective_required_service

pytestmark = pytest.mark.asyncio


def _user(tmp_path, *, admin: bool = False) -> CurrentUser:
    uid = "u_admin" if admin else "u_alice"
    return CurrentUser(
        id=uid,
        username="admin" if admin else "alice",
        role="admin" if admin else "user",
        scope=UserScope(
            kind="admin" if admin else "user",
            user_id=uid,
            root=tmp_path / uid,
        ),
    )


def _patch_settings(monkeypatch, block: dict[str, Any] | None) -> None:
    monkeypatch.setattr(
        "kagweb.services.agent_loop.settings.get_agent_loop_settings",
        lambda: block if block is not None else {},
    )


# ── what a turn needs ────────────────────────────────────────────────────


def test_chat_needs_the_agent_backend_when_one_is_configured(monkeypatch) -> None:
    # ``hermes`` is the HTTP family: it runs the loop in its own service and
    # starts nothing on this host.
    _patch_settings(
        monkeypatch,
        {"profiles": [{"id": "p", "preset": "hermes", "enabled": True}], "primary": "p"},
    )
    assert _effective_required_service("chat") == "agent_loop"


def test_a_cli_backend_needs_the_local_process_grant(monkeypatch) -> None:
    """The CLI family spawns a child as the server user, so driving it is code
    execution on this host — a different, stricter gate than any HTTP backend."""
    _patch_settings(
        monkeypatch,
        {"profiles": [{"id": "p", "preset": "claude-code", "enabled": True}], "primary": "p"},
    )
    assert _effective_required_service("chat") == "agent_loop_cli"


def test_an_acp_backend_counts_as_the_cli_family(monkeypatch) -> None:
    # `intellect` is family="cli" with transport="acp": same local-process risk,
    # so it must land on the same gate without a special case for the transport.
    _patch_settings(
        monkeypatch,
        {"profiles": [{"id": "p", "preset": "intellect", "enabled": True}], "primary": "p"},
    )
    assert _effective_required_service("chat") == "agent_loop_cli"


def test_an_unknown_preset_does_not_get_the_strict_grant(monkeypatch) -> None:
    # preset_family returns "" for an unrecognised name; only a positively
    # identified CLI preset should demand the stricter grant.
    _patch_settings(
        monkeypatch,
        {"profiles": [{"id": "p", "preset": "some-new-thing", "enabled": True}], "primary": "p"},
    )
    assert _effective_required_service("chat") == "agent_loop"


def test_chat_needs_the_llm_when_no_backend_is_configured(monkeypatch) -> None:
    # No profiles → the framework-shell stub. Nothing drives the turn, so the
    # historical LLM gate is the right one.
    _patch_settings(monkeypatch, {"profiles": [], "primary": ""})
    assert _effective_required_service("chat") == "llm"


def test_a_preloaded_block_is_honoured(monkeypatch) -> None:
    """The caller may pass the block it already read, so the gate does not
    become a second hit on the settings file within one turn."""
    # Settings read would fail, but the caller supplies the block.
    monkeypatch.setattr(
        "kagweb.services.agent_loop.settings.get_agent_loop_settings",
        lambda: (_ for _ in ()).throw(AssertionError("should not read settings")),
    )
    supplied = {"profiles": [{"id": "p", "preset": "codex", "enabled": True}], "primary": "p"}
    assert _effective_required_service("chat", agent_loop_block=supplied) == "agent_loop_cli"


def test_a_disabled_primary_means_no_backend(monkeypatch) -> None:
    # Pausing the backend on purpose must not leave the turn ungated.
    _patch_settings(
        monkeypatch,
        {"profiles": [{"id": "p", "preset": "hermes", "enabled": False}], "primary": "p"},
    )
    assert _effective_required_service("chat") == "llm"


def test_unreadable_settings_fall_back_to_the_llm_gate(monkeypatch) -> None:
    # Never turn a settings failure into "allow".
    def _boom() -> dict[str, Any]:
        raise OSError("settings unreadable")

    monkeypatch.setattr("kagweb.services.agent_loop.settings.get_agent_loop_settings", _boom)
    assert _effective_required_service("chat") == "llm"


def test_other_capabilities_keep_their_declared_service(monkeypatch) -> None:
    # A capability that is not chat reports whatever its manifest declares;
    # an unknown one defaults to the LLM gate.
    assert _effective_required_service("does_not_exist") == "llm"


# ── the agent_loop access dimension ──────────────────────────────────────


def test_agent_loop_access_defaults_to_allowed(tmp_path, monkeypatch) -> None:
    """An absent grant follows the deployment: configuring a backend is the
    admin's decision that users may use it. Requiring an explicit per-user
    grant would make a multi-user deployment unusable out of the box."""
    monkeypatch.setattr(
        model_access,
        "load_grant",
        lambda _uid: {"models": {"llm": []}},  # no agent_loop key at all
    )
    monkeypatch.setattr(model_access, "admin_catalog", lambda: {"services": {}})
    token = set_current_user(_user(tmp_path))
    try:
        assert model_access.has_capability_access("agent_loop") is True
    finally:
        reset_current_user(token)


def test_agent_loop_access_can_be_explicitly_suspended(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        model_access,
        "load_grant",
        lambda _uid: {"models": {"llm": []}, "agent_loop": False},
    )
    monkeypatch.setattr(model_access, "admin_catalog", lambda: {"services": {}})
    token = set_current_user(_user(tmp_path))
    try:
        assert model_access.has_capability_access("agent_loop") is False
    finally:
        reset_current_user(token)


def test_admin_bypasses_the_agent_loop_dimension(tmp_path, monkeypatch) -> None:
    def _boom(_uid: str | None = None):
        raise AssertionError("admins must not consult the grant view")

    monkeypatch.setattr(model_access, "redacted_model_access", _boom)
    token = set_current_user(_user(tmp_path, admin=True))
    try:
        assert model_access.has_capability_access("agent_loop_cli") is True
    finally:
        reset_current_user(token)


# ── the agent_loop_cli dimension (opt-in, the opposite direction) ────────


def _grant_view(monkeypatch, grant: dict) -> None:
    monkeypatch.setattr(model_access, "load_grant", lambda _uid: grant)
    monkeypatch.setattr(model_access, "admin_catalog", lambda: {"services": {}})


def test_agent_loop_cli_denied_by_default(tmp_path, monkeypatch) -> None:
    """Starting a local agent process is code execution as the server user, so
    an absent grant must deny — fail-closed, and the opposite default from
    ``agent_loop``. An existing deployment has no such key, so upgrading
    tightens rather than loosens."""
    _grant_view(monkeypatch, {"models": {"llm": []}})  # no key at all
    token = set_current_user(_user(tmp_path))
    try:
        assert model_access.has_capability_access("agent_loop_cli") is False
    finally:
        reset_current_user(token)


def test_agent_loop_cli_requires_an_explicit_true(tmp_path, monkeypatch) -> None:
    for value, expected in ((False, False), (True, True), (None, False)):
        _grant_view(monkeypatch, {"models": {"llm": []}, "agent_loop_cli": value})
        token = set_current_user(_user(tmp_path))
        try:
            assert model_access.has_capability_access("agent_loop_cli") is expected, value
        finally:
            reset_current_user(token)


def test_the_two_agent_dimensions_default_opposite_ways(tmp_path, monkeypatch) -> None:
    """One absent grant, two different verdicts — this is the whole point of the
    split: the HTTP family is on by default, the local-process family is not."""
    _grant_view(monkeypatch, {"models": {"llm": []}})
    token = set_current_user(_user(tmp_path))
    try:
        assert model_access.has_capability_access("agent_loop") is True
        assert model_access.has_capability_access("agent_loop_cli") is False
    finally:
        reset_current_user(token)
