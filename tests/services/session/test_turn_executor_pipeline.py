"""End-to-end ``_run_turn`` regression tests through ``TurnRuntimeManager``.

These exercise the full executor pipeline (request prepare → context
assembly → capability → persistence → terminal DONE), which the unit suite
otherwise skips. They pin two behaviors:

* bare turns — a deployment with no configured model completes the stub
  chat turn instead of failing it (NoModelConfiguredError → None config);
* answer persistence — the streamed content bytes become the persisted
  assistant message (regression: an earlier refactor made
  ``_assemble_persisted_answer`` require two arguments while the executor
  passed one, crashing every completed turn);
* broken configs — a model that exists but is misconfigured fails the
  turn with its real error instead of silently completing as a stub.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from kagweb.core.stream import StreamEvent, StreamEventType
from kagweb.services.llm.exceptions import LLMConfigError, NoModelConfiguredError
from kagweb.services.session.sqlite_store import SQLiteSessionStore
from kagweb.services.session.turn_runtime import TurnRuntimeManager

pytestmark = pytest.mark.asyncio


class _ScriptedEngine:
    """Turn engine stand-in that replays fixed stream events."""

    def __init__(self, *events: StreamEvent) -> None:
        self._events = list(events)

    async def execute(self, context: Any):
        for event in self._events:
            yield event


@pytest.fixture
def store(tmp_path: Path) -> SQLiteSessionStore:
    return SQLiteSessionStore(tmp_path / "chat_history.db")


@pytest.fixture
def stub_workspace(monkeypatch, tmp_path: Path):
    """Keep the workspace event mirror off the developer's real tree."""

    class _StubPathService:
        def get_task_workspace(self, feature: str, task_id: str) -> Path:
            return tmp_path / "workspace" / feature / task_id

    monkeypatch.setattr(
        "kagweb.services.session.turns.lifecycle.get_path_service",
        lambda: _StubPathService(),
    )
    return tmp_path / "workspace"


async def _run_turn_and_wait(runtime: TurnRuntimeManager, payload: dict) -> dict:
    session, turn = await runtime.start_turn(payload)
    execution = runtime._executions.get(turn["id"])
    assert execution is not None and execution.task is not None
    await execution.task
    return await runtime.store.get_turn(turn["id"])


def _stub_payload(content: str = "hello") -> dict:
    return {"content": content, "capability": "chat", "language": "en", "tools": []}


async def test_bare_turn_completes_with_stub_notice(store, stub_workspace, monkeypatch) -> None:
    """No model configured → stub capability still completes the turn."""
    monkeypatch.setattr(
        "kagweb.services.model_selection.runtime.activate_llm_selection",
        lambda selection: (_ for _ in ()).throw(NoModelConfiguredError("no model")),
    )
    # Title generation must skip (not raise) on a bare deployment.
    monkeypatch.setattr("kagweb.services.llm.config.has_configured_llm", lambda: False)

    runtime = TurnRuntimeManager(store=store)
    final = await _run_turn_and_wait(runtime, _stub_payload())

    assert final is not None
    assert final["status"] == "completed", final
    messages = await store.get_messages(final["session_id"])
    roles = [m["role"] for m in messages]
    assert roles == ["user", "assistant"]
    assert "framework shell" in messages[1]["content"]


async def test_streamed_content_is_persisted_as_answer(store, stub_workspace) -> None:
    """The regression pin: streamed CONTENT bytes become the persisted answer.

    A previous refactor changed ``_assemble_persisted_answer`` to require a
    second argument while the executor passed one — every completed turn
    crashed with TypeError. This test runs the real executor assembly path
    with a scripted engine and asserts the exact persisted string.
    """
    engine = _ScriptedEngine(
        StreamEvent(type=StreamEventType.CONTENT, source="chat", content="Hello "),
        StreamEvent(type=StreamEventType.CONTENT, source="chat", content="world"),
    )
    runtime = TurnRuntimeManager(store=store, turn_engine=engine)

    session, turn = await runtime.start_turn(_stub_payload())
    execution = runtime._executions.get(turn["id"])
    assert execution is not None and execution.task is not None
    await execution.task

    final = await store.get_turn(turn["id"])
    assert final is not None and final["status"] == "completed"
    messages = await store.get_messages(session["id"])
    assistant = next(m for m in messages if m["role"] == "assistant")
    assert assistant["content"] == "Hello world"

    done_events = [e for e in execution.events if e["type"] == "done"]
    assert done_events, "executor must publish a terminal DONE event"
    assert done_events[-1]["metadata"].get("status") == "completed"
    assert done_events[-1]["metadata"].get("assistant_message_id") == assistant["id"]


async def test_broken_model_config_fails_turn_with_real_error(
    store, stub_workspace, monkeypatch
) -> None:
    """A configured-but-broken model must NOT degrade into a stub turn."""
    monkeypatch.setattr(
        "kagweb.services.model_selection.runtime.activate_llm_selection",
        lambda selection: (_ for _ in ()).throw(
            LLMConfigError("OpenAI API key is not configured. Set it in Settings > Catalog.")
        ),
    )

    runtime = TurnRuntimeManager(store=store)
    final = await _run_turn_and_wait(runtime, _stub_payload())

    assert final is not None
    assert final["status"] == "failed", final
    assert "API key is not configured" in str(final.get("error") or "")


async def test_cancelled_turn_persists_partial_content(store, stub_workspace, monkeypatch) -> None:
    """Cancellation mid-stream still persists the streamed prefix."""

    class _SlowEngine:
        async def execute(self, context: Any):
            yield StreamEvent(type=StreamEventType.CONTENT, source="chat", content="partial")
            await asyncio.sleep(30)
            yield StreamEvent(type=StreamEventType.CONTENT, source="chat", content="never")

    runtime = TurnRuntimeManager(store=store, turn_engine=_SlowEngine())
    session, turn = await runtime.start_turn(_stub_payload())
    execution = runtime._executions.get(turn["id"])
    assert execution is not None and execution.task is not None

    async def _wait_for_content() -> None:
        while not any(e["type"] == "content" for e in list(execution.events)):
            await asyncio.sleep(0.01)

    await asyncio.wait_for(_wait_for_content(), timeout=5)
    cancelled = await runtime.cancel_turn(turn["id"])
    assert cancelled

    final = await store.get_turn(turn["id"])
    assert final is not None
    assert final["status"] == "cancelled"
    messages = await store.get_messages(session["id"])
    assistant = [m for m in messages if m["role"] == "assistant"]
    assert assistant and assistant[-1]["content"].strip() == "partial"
