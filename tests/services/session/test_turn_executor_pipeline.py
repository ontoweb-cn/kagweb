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
from types import SimpleNamespace
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


class _FakeAgentBackend:
    """Minimal agent-loop backend for the full-pipeline delegation test."""

    name = "fake"

    def __init__(self, *events: Any) -> None:
        self.events = list(events)
        self.requests: list[Any] = []

    async def run(self, request: Any):
        self.requests.append(request)
        for event in self.events:
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
    return {"content": content, "capability": "chat", "language": "en"}


async def test_bare_turn_completes_with_stub_notice(store, stub_workspace, monkeypatch) -> None:
    """No model configured → stub capability still completes the turn."""
    monkeypatch.setattr(
        "kagweb.services.model_selection.runtime.activate_llm_selection",
        lambda selection: (_ for _ in ()).throw(NoModelConfiguredError("no model")),
    )
    # Title generation must skip (not raise) on a bare deployment.
    monkeypatch.setattr("kagweb.services.llm.config.has_configured_llm", lambda: False)
    monkeypatch.setattr(
        "kagweb.capabilities.chat.capability.get_agent_loop_settings",
        lambda: {"backend": ""},
    )

    runtime = TurnRuntimeManager(store=store)
    final = await _run_turn_and_wait(runtime, _stub_payload())

    assert final is not None
    assert final["status"] == "completed", final
    messages = await store.get_messages(final["session_id"])
    roles = [m["role"] for m in messages]
    assert roles == ["user", "assistant"]
    # The notice is localized (interface settings decide zh/en); accept the
    # i18n entry for either language rather than one fixed wording.
    from kagweb.services.i18n import t

    assert messages[1]["content"] in {
        t("chat.stub_notice", language="en"),
        t("chat.stub_notice", language="zh"),
    }


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
    monkeypatch.setattr(
        "kagweb.capabilities.chat.capability.get_agent_loop_settings",
        lambda: {"backend": ""},
    )

    runtime = TurnRuntimeManager(store=store)
    final = await _run_turn_and_wait(runtime, _stub_payload())

    assert final is not None
    assert final["status"] == "failed", final
    assert "API key is not configured" in str(final.get("error") or "")


async def test_agent_loop_backend_streams_and_persists(store, stub_workspace, monkeypatch) -> None:
    """Configured agent-loop backend: events map to the stream, content persists."""
    from kagweb.services.agent_loop.protocol import AgentLoopEvent, AgentLoopRequest

    backend = _FakeAgentBackend(
        AgentLoopEvent("thinking", text="pondering"),
        AgentLoopEvent("content", text="the answer"),
        AgentLoopEvent("tool_call", name="shell", data={"args": {"command": "ls"}}),
        AgentLoopEvent("tool_result", name="shell", text="file.txt"),
        AgentLoopEvent("usage", data={"input_tokens": 10, "output_tokens": 5}),
    )
    monkeypatch.setattr(
        "kagweb.capabilities.chat.capability.get_agent_loop_settings",
        lambda: {"backend": "fake", "session_workspace": False},
    )
    monkeypatch.setattr(
        "kagweb.capabilities.chat.capability.build_agent_loop_backend",
        lambda settings: backend,
    )
    # The turn needs a resolvable model config only for context budgeting;
    # a bare deployment delegates entirely to the agent loop.
    monkeypatch.setattr(
        "kagweb.services.model_selection.runtime.activate_llm_selection",
        lambda selection: (
            SimpleNamespace(model="agent-loop", context_window=None, max_tokens=None),
            None,
        ),
    )
    monkeypatch.setattr("kagweb.services.llm.config.has_configured_llm", lambda: False)

    runtime = TurnRuntimeManager(store=store)
    session, turn = await runtime.start_turn(_stub_payload("what files?"))
    execution = runtime._executions.get(turn["id"])
    assert execution is not None and execution.task is not None
    await execution.task

    final = await store.get_turn(turn["id"])
    assert final is not None and final["status"] == "completed", final

    # The backend saw the composed request.
    assert len(backend.requests) == 1
    request: AgentLoopRequest = backend.requests[0]
    assert request.prompt == "what files?"
    assert request.session_id == session["id"]

    # Events were mapped onto the turn stream.
    kinds = [e["type"] for e in execution.events]
    assert "thinking" in kinds
    assert "tool_call" in kinds
    assert "tool_result" in kinds

    # Content became the persisted answer; the RESULT envelope carries the
    # backend identity.
    messages = await store.get_messages(session["id"])
    assistant = next(m for m in messages if m["role"] == "assistant")
    assert assistant["content"] == "the answer"
    result_events = [e for e in execution.events if e["type"] == "result"]
    assert result_events
    agent_meta = result_events[-1]["metadata"].get("agent_loop") or {}
    assert agent_meta.get("backend") == "fake"
    assert agent_meta.get("usage", {}).get("input_tokens") == 10


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


async def test_attachment_paths_reach_the_prompt_only_for_cli_backends(
    store, stub_workspace, monkeypatch
) -> None:
    """Materialized copies surface as manifest path rows — but only when the
    configured backend actually runs in a filesystem workspace (CLI/ACP);
    the HTTP family and bare deployments get the pathless manifest."""
    from kagweb.services.agent_loop.protocol import AgentLoopEvent

    async def _fake_materialize(session_id, records, *, store=None):
        return {r["id"]: f"/tmp/fake-ws/attachments/{r['id']}_{r['filename']}" for r in records}

    monkeypatch.setattr(
        "kagweb.services.session.attachment_workspace.materialize_attachments",
        _fake_materialize,
    )

    payload = {
        **_stub_payload("read this"),
        # url pre-set: the regenerate shortcut — no store round-trip needed.
        "attachments": [
            {
                "type": "file",
                "url": "/files/attachments/s1/a1_notes.txt",
                "base64": "",
                "filename": "notes.txt",
                "mime_type": "text/plain",
                "id": "a1",
                "extracted_text": "shared body text",
            }
        ],
    }

    async def _run_with_family(family: str):
        local_backend = _FakeAgentBackend(AgentLoopEvent("content", text="ok"))
        monkeypatch.setattr(
            "kagweb.capabilities.chat.capability.get_agent_loop_settings",
            lambda: {"backend": "fake", "session_workspace": False},
        )
        monkeypatch.setattr(
            "kagweb.capabilities.chat.capability.build_agent_loop_backend",
            lambda settings: local_backend,
        )
        monkeypatch.setattr(
            "kagweb.services.model_selection.runtime.activate_llm_selection",
            lambda selection: (
                SimpleNamespace(model="agent-loop", context_window=None, max_tokens=None),
                None,
            ),
        )
        monkeypatch.setattr("kagweb.services.llm.config.has_configured_llm", lambda: False)
        # The family reaches the executor on the payload (stamped by the
        # request preparer from the profile's *preset* — the real registry
        # derivation runs). The direct probe is pinned to the OPPOSITE
        # family: if the executor ever consulted it again, both legs below
        # would flip and fail.
        preset_for_family = {"cli": "claude-code", "http": "intellect-team"}
        monkeypatch.setattr(
            "kagweb.services.agent_loop.settings.resolve_primary_profile",
            lambda block: {"preset": preset_for_family[family]},
        )
        monkeypatch.setattr(
            "kagweb.services.session.turns.executor._primary_profile_family",
            lambda: "http" if family == "cli" else "cli",
        )
        runtime = TurnRuntimeManager(store=store)
        session, turn = await runtime.start_turn(payload)
        execution = runtime._executions.get(turn["id"])
        assert execution is not None and execution.task is not None
        await execution.task
        final = await store.get_turn(turn["id"])
        assert final is not None and final["status"] == "completed", final
        return local_backend.requests[0]

    cli_request = await _run_with_family("cli")
    assert "path: /tmp/fake-ws/attachments/a1_notes.txt" in cli_request.prompt
    assert "read it when the preview is not enough" in cli_request.prompt

    http_request = await _run_with_family("http")
    assert "path:" not in http_request.prompt
    assert "notes.txt" in http_request.prompt  # preview row, just no copy
    assert "shared body text" in http_request.prompt
