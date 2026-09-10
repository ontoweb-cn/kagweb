"""The per-turn access gate, and how it resolves what a turn needs.

Regression cover for `backend-llm-deployment.md` P0-1: the gate used to demand
an *LLM* grant for every non-admin turn, even when the chat capability was
delegating to an agent backend that brings its own model. In a pure agent-loop
deployment that rejected every non-admin turn, so the deployment served admins
only — and nothing in the suite noticed, because the gate had no tests at all.

The second half drives the real `start_turn`, which is where the first fix was
wrong: the gate was written correctly but placed inside the "no pinned
llm_selection" branch, so a client that *did* pin a selection skipped it. Unit
tests over the resolver could never have caught that — the branch structure is
the thing under test.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from kagweb.multi_user import model_access
from kagweb.multi_user.context import reset_current_user, set_current_user
from kagweb.multi_user.models import CurrentUser, UserScope
from kagweb.services.session.sqlite_store import SQLiteSessionStore
from kagweb.services.session.turn_runtime import TurnRuntimeManager
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


# ── the gate as assembled in start_turn ──────────────────────────────────
#
# The unit tests above prove the resolver and the access predicate are right;
# they say nothing about whether `start_turn` actually calls them on every
# path. It did not: a pinned `llm_selection` — a client-supplied protocol
# field — took the other branch and skipped the resource check entirely.
# These drive the real entry point so the *structure* is under test.

_CLI_SETTINGS = {
    "profiles": [{"id": "p", "preset": "claude-code", "enabled": True}],
    "primary": "p",
}


def _patch_agent_loop(monkeypatch, settings: dict[str, Any]) -> None:
    """Install an agent-loop block the way the runtime actually sees one.

    ``load_system`` normalizes the block before it is returned, so callers get
    clamped/defaulted values. Stubbing the accessor with a raw literal would
    skip that step and let a test assert behaviour production never sees — so
    the literal is pushed through the real normalizer here.
    """
    from kagweb.services.config.runtime_settings import (
        RuntimeSettingsService,
    )

    service = RuntimeSettingsService.get_instance()
    normalized = service._normalize_agent_loop({"agent_loop": settings or {}})

    monkeypatch.setattr(
        "kagweb.services.agent_loop.settings.get_agent_loop_settings",
        lambda: normalized,
    )


def _patch_grant(monkeypatch, grant: dict[str, Any]) -> None:
    """Install a grant view derived from *grant*, so the stub stays honest.

    The LLM rows and the ``agent_loop_cli`` availability both follow what the
    grant actually holds — a stub that always reported a usable model would
    send the non-admin path into selection validation the real grant never
    justifies.
    """
    llm_rows = [
        {
            "profile_id": str(item.get("profile_id") or ""),
            "model_id": "m1",
            "name": "m1",
            "model": "m1",
            "available": True,
        }
        for item in (grant.get("models", {}) or {}).get("llm", []) or []
        if isinstance(item, dict)
    ]
    monkeypatch.setattr(model_access, "load_grant", lambda _uid: grant)
    monkeypatch.setattr(model_access, "admin_catalog", lambda: {"services": {}})
    monkeypatch.setattr(
        model_access,
        "redacted_model_access",
        lambda _uid=None: {
            "llm": llm_rows,
            "agent_loop": [{"source": "deployment", "available": True}],
            "agent_loop_cli": [
                {"source": "deployment", "available": grant.get("agent_loop_cli") is True}
            ],
        },
    )


async def _start_turn_error(tmp_path, monkeypatch, *, payload_extra: dict) -> str:
    """Run a real turn and return the error text, or "" when it was accepted.

    The access gate raises synchronously inside ``start_turn``, so a rejection
    is returned straight away. On acceptance the turn task is awaited too, so no
    work is left running past the test.
    """
    store = SQLiteSessionStore(tmp_path / "chat_history.db")
    runtime = TurnRuntimeManager(store)
    session = await store.ensure_session(None)
    payload = {
        "capability": "chat",
        "content": "hello",
        "session_id": session["id"],
        "language": "en",
        **payload_extra,
    }
    try:
        _session, turn = await runtime.start_turn(payload)
    except Exception as exc:  # noqa: BLE001 - the message is the subject
        return str(exc)
    execution = runtime._executions.get(turn["id"])
    if execution is not None and execution.task is not None:
        await execution.task
    return ""


#: One granted LLM, so the pinned-selection branch has something to validate.
_GRANTED_LLM = {"models": {"llm": [{"profile_id": "p1", "model_ids": ["m1"]}]}}


async def test_a_pinned_selection_cannot_skip_the_agent_process_gate(tmp_path, monkeypatch) -> None:
    """The regression: `llm_selection` is client-supplied, and pinning one used
    to route around the resource check. A user with a granted LLM could start a
    CLI agent process without the grant that exists to permit it."""
    _patch_agent_loop(monkeypatch, _CLI_SETTINGS)
    _patch_grant(monkeypatch, _GRANTED_LLM)  # no agent_loop_cli
    token = set_current_user(_user(tmp_path))
    try:
        error = await _start_turn_error(
            tmp_path,
            monkeypatch,
            payload_extra={"llm_selection": {"profile_id": "p1", "model_id": "m1"}},
        )
    finally:
        reset_current_user(token)

    assert "local process" in error, error


async def test_the_gate_also_holds_without_a_pinned_selection(tmp_path, monkeypatch) -> None:
    _patch_agent_loop(monkeypatch, _CLI_SETTINGS)
    _patch_grant(monkeypatch, _GRANTED_LLM)
    token = set_current_user(_user(tmp_path))
    try:
        error = await _start_turn_error(tmp_path, monkeypatch, payload_extra={})
    finally:
        reset_current_user(token)

    assert "local process" in error, error


async def test_the_gate_is_consulted_on_both_paths(tmp_path, monkeypatch) -> None:
    """Assert the *structure*: whether or not a selection is pinned, the
    resolver runs. This is the property the first fix violated."""
    from kagweb.services.session.turns import request_preparer

    seen: list[str] = []
    real = request_preparer._effective_required_service

    def _spy(capability: str, **kwargs: Any) -> str:
        resolved = real(capability, **kwargs)
        seen.append(resolved)
        return resolved

    monkeypatch.setattr(request_preparer, "_effective_required_service", _spy)
    _patch_agent_loop(monkeypatch, _CLI_SETTINGS)
    _patch_grant(monkeypatch, _GRANTED_LLM)
    token = set_current_user(_user(tmp_path))
    try:
        for extra in ({}, {"llm_selection": {"profile_id": "p1", "model_id": "m1"}}):
            seen.clear()
            await _start_turn_error(tmp_path, monkeypatch, payload_extra=extra)
            assert seen == ["agent_loop_cli"], (extra, seen)
    finally:
        reset_current_user(token)


async def test_an_http_backend_needs_no_cli_grant(tmp_path, monkeypatch) -> None:
    """The HTTP family starts no local process, so the stricter grant must not
    apply to it: this is what keeps multi-user deployments usable."""
    _patch_agent_loop(
        monkeypatch,
        {"profiles": [{"id": "p", "preset": "hermes", "enabled": True}], "primary": "p"},
    )
    _patch_grant(monkeypatch, _GRANTED_LLM)  # no agent_loop_cli
    token = set_current_user(_user(tmp_path))
    try:
        error = await _start_turn_error(tmp_path, monkeypatch, payload_extra={})
    finally:
        reset_current_user(token)

    assert "local process" not in error, error


async def test_an_admin_is_never_gated(tmp_path, monkeypatch) -> None:
    _patch_agent_loop(monkeypatch, _CLI_SETTINGS)
    _patch_grant(monkeypatch, {"models": {"llm": []}})  # nothing granted at all
    token = set_current_user(_user(tmp_path, admin=True))
    try:
        error = await _start_turn_error(tmp_path, monkeypatch, payload_extra={})
    finally:
        reset_current_user(token)

    assert "local process" not in error, error


# ── the agent backend's context window reaches the budget ────────────────
#
# First line of defence: the hand-off key is internal, and ``TurnRequest``
# forbids extra fields, so a client cannot inject it wholesale.


async def test_a_client_supplied_context_window_key_is_rejected(tmp_path, monkeypatch) -> None:
    """The executor trusts ``agent_loop_context_window`` on the payload, so the
    key must not be a client-suppliable turn field. The forbid on
    ``TurnRequest`` is that guarantee — pinned here, because if it were ever
    relaxed, a client value would ride past the preparer and drive the history
    budget."""
    _patch_agent_loop(monkeypatch, _CLI_WITH_WINDOW)
    _patch_grant(monkeypatch, {"models": {"llm": []}, "agent_loop_cli": True})
    error = await _start_turn_error(
        tmp_path,
        monkeypatch,
        payload_extra={"agent_loop_context_window": 5_000_000},
    )
    assert "Extra inputs are not permitted" in error, error


# ── the agent backend's context window reaches the budget ────────────────
#
# The executor builds the context before the capability resolves the agent-loop
# profile, so the window has to travel on the payload. It is injected for
# admins too — history budgeting is not an access question.

_CLI_WITH_WINDOW = {
    "profiles": [{"id": "p", "preset": "claude-code", "enabled": True, "context_window": 200_000}],
    "primary": "p",
}


async def _turn_payload(tmp_path, monkeypatch, *, admin: bool) -> dict[str, Any]:
    """Run a real turn and return the payload the executor ran with.

    Asserts on the payload rather than intercepting ``ContextBuilder.build``:
    the payload is the contract between the request preparer (which knows the
    profile's window) and the executor (which builds the context before the
    capability resolves that profile). The builder's own handling of the
    override is covered by unit tests in ``test_context_builder.py``.
    """
    store = SQLiteSessionStore(tmp_path / "chat_history.db")
    runtime = TurnRuntimeManager(store)
    session = await store.ensure_session(None)
    captured: dict[str, Any] = {}
    real_run_turn = runtime._run_turn

    async def _spy(execution):  # noqa: ANN001
        captured.update(execution.payload)
        return await real_run_turn(execution)

    monkeypatch.setattr(runtime, "_run_turn", _spy)

    token = set_current_user(_user(tmp_path, admin=admin))
    try:
        _session, turn = await runtime.start_turn(
            {
                "capability": "chat",
                "content": "hi",
                "session_id": session["id"],
                "language": "en",
            }
        )
        execution = runtime._executions.get(turn["id"])
        if execution is not None and execution.task is not None:
            await execution.task
    finally:
        reset_current_user(token)
    return captured


@pytest.mark.parametrize("admin", [False, True])
async def test_the_profile_context_window_travels_on_the_payload(
    tmp_path, monkeypatch, admin
) -> None:
    """Admins need the window too — sizing the history budget is not an access
    question, and an agent-loop turn has no ``llm_config`` to derive it from."""
    _patch_agent_loop(monkeypatch, _CLI_WITH_WINDOW)
    # No LLM grant: for a non-admin that keeps the turn on the "no pinned
    # selection" path, which is what the payload assertion is about. Admins are
    # ungated either way.
    _patch_grant(monkeypatch, {"models": {"llm": []}, "agent_loop_cli": True})
    payload = await _turn_payload(tmp_path, monkeypatch, admin=admin)
    assert payload.get("agent_loop_context_window") == 200_000


async def test_no_window_configured_writes_zero(tmp_path, monkeypatch) -> None:
    """The key is always written now — that is what keeps a client value from
    surviving. An unconfigured profile writes 0, which the executor maps to no
    override, so the budget chain still falls back exactly as before."""
    _patch_agent_loop(monkeypatch, _CLI_SETTINGS)  # no context_window key
    _patch_grant(monkeypatch, {"models": {"llm": []}, "agent_loop_cli": True})
    payload = await _turn_payload(tmp_path, monkeypatch, admin=True)
    assert payload.get("agent_loop_context_window") == 0


async def test_the_window_key_is_not_persisted_with_the_user_message(tmp_path, monkeypatch) -> None:
    """It is an internal hand-off key, not part of the request the session
    stores — a snapshot carrying it would resurface on regenerate."""
    from kagweb.services.session._turn_runtime_shared import _request_snapshot_metadata

    snapshot = _request_snapshot_metadata(
        payload={"agent_loop_context_window": 200_000, "language": "en"},
        content="hi",
        capability="chat",
        config={},
        attachments=[],
        history_references=[],
        partner_group_references=[],
        persona="",
        llm_selection=None,
    )
    assert "agent_loop_context_window" not in snapshot["request_snapshot"]
