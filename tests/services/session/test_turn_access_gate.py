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
    _patch_settings(
        monkeypatch,
        {"profiles": [{"id": "p", "preset": "hermes", "enabled": True}], "primary": "p"},
    )
    assert _effective_required_service("chat") == "agent_loop"


def test_chat_needs_the_llm_when_no_backend_is_configured(monkeypatch) -> None:
    # No profiles → the framework-shell stub. Nothing drives the turn, so the
    # historical LLM gate is the right one.
    _patch_settings(monkeypatch, {"profiles": [], "primary": ""})
    assert _effective_required_service("chat") == "llm"


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
        assert model_access.has_capability_access("agent_loop") is True
    finally:
        reset_current_user(token)
