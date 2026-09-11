"""ChatCapability: shell-stub fallback vs agent-loop delegation."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from kagweb.capabilities.chat.capability import ChatCapability
from kagweb.core.context import UnifiedContext
from kagweb.core.stream import StreamEvent
from kagweb.runtime.stream_bus import StreamBus
from kagweb.services.agent_loop.protocol import AgentLoopEvent

pytestmark = pytest.mark.asyncio


class _RecordingBackend:
    name = "recording"
    #: The CLI family is the one that runs in a working directory.
    uses_workdir = True

    def __init__(self, events: list[AgentLoopEvent]) -> None:
        self.events = events
        self.requests: list[Any] = []

    async def run(self, request: Any):
        self.requests.append(request)
        for event in self.events:
            yield event


async def _run_capability(context: UnifiedContext, bus: StreamBus) -> list[tuple[str, str]]:
    collected: list[tuple[str, str]] = []

    async def collect() -> None:
        async for event in bus.subscribe():
            collected.append((event.type.value, event.content))

    task = asyncio.create_task(collect())
    await ChatCapability().run(context, bus)
    await bus.close()
    await task
    return collected


async def _run_capability_events(context: UnifiedContext, bus: StreamBus) -> list[StreamEvent]:
    """Like ``_run_capability`` but keeps the metadata the UI reads."""
    collected: list[StreamEvent] = []

    async def collect() -> None:
        async for event in bus.subscribe():
            collected.append(event)

    task = asyncio.create_task(collect())
    await ChatCapability().run(context, bus)
    await bus.close()
    await task
    return collected


def _configure(monkeypatch, backend: Any) -> None:
    monkeypatch.setattr(
        "kagweb.capabilities.chat.capability.get_agent_loop_settings",
        lambda: {"backend": "recording", "session_workspace": False},
    )
    monkeypatch.setattr(
        "kagweb.capabilities.chat.capability.build_agent_loop_backend",
        lambda settings: backend,
    )


async def test_unconfigured_backend_emits_shell_notice(monkeypatch) -> None:
    monkeypatch.setattr(
        "kagweb.capabilities.chat.capability.get_agent_loop_settings",
        lambda: {"backend": ""},
    )
    context = UnifiedContext(session_id="s", user_message="hi", language="en")
    events = await _run_capability(context, StreamBus())

    kinds = [kind for kind, _ in events]
    assert kinds == ["content", "result"]
    # The notice is localized (interface settings decide zh/en); accept the
    # i18n entry for either language rather than one fixed wording.
    from kagweb.services.i18n import t

    assert events[0][1] in {
        t("chat.stub_notice", language="en"),
        t("chat.stub_notice", language="zh"),
    }
    assert context.capability_output.answer_published is True


async def test_configured_backend_streams_and_publishes_result(monkeypatch) -> None:
    backend = _RecordingBackend(
        [
            AgentLoopEvent("thinking", text="pondering"),
            AgentLoopEvent("content", text="a"),
            AgentLoopEvent("content", text="b"),
            AgentLoopEvent("tool_call", name="shell", data={"args": {"command": "ls"}}),
            AgentLoopEvent("tool_result", name="shell", text="files"),
            AgentLoopEvent("error", text="minor hiccup"),
        ]
    )
    monkeypatch.setattr(
        "kagweb.capabilities.chat.capability.get_agent_loop_settings",
        lambda: {"backend": "recording", "session_workspace": False},
    )
    monkeypatch.setattr(
        "kagweb.capabilities.chat.capability.build_agent_loop_backend",
        lambda settings: backend,
    )
    context = UnifiedContext(
        session_id="sess-1",
        user_message="question",
        source_manifest="- id:1 file.txt",
        language="en",
        conversation_history=[
            {"role": "user", "content": "earlier"},
            {"role": "assistant", "content": "reply"},
            {"role": "tool", "content": "filtered out"},
        ],
    )
    events = await _run_capability(context, StreamBus())

    kinds = [kind for kind, _ in events]
    assert kinds == [
        "progress",  # round opens
        "thinking",
        "content",
        "content",
        "progress",  # the tool call ends the round
        "tool_call",
        "tool_result",
        "observation",  # the collapsed row's excerpt
        "error",
        "progress",  # the pass settles
        "result",
    ]
    # Grounding blocks are folded into the prompt ahead of the user message.
    request = backend.requests[0]
    # The agent-loop prompt opens with the grounding blocks (source manifest
    # first now that the persona block is gone), then the user message.
    assert request.prompt.startswith("Attached sources:")
    assert "file.txt" in request.prompt
    assert request.prompt.endswith("question")
    assert request.history == [
        {"role": "user", "content": "earlier"},
        {"role": "assistant", "content": "reply"},
    ]
    assert request.session_id == "sess-1"
    # Content events are separate blocks, so the answer joins them as
    # paragraphs (and the live stream carries the break with the later block);
    # the RESULT envelope names the backend that produced it.
    contents = [payload for kind, payload in events if kind == "content"]
    assert contents == ["a", "\n\nb"]
    assert context.capability_output.agent_output == "a\n\nb"
    assert context.capability_output.answer_published is True
    result = events[-1]
    assert result[0] == "result"


async def test_agent_loop_events_carry_the_trace_contract(monkeypatch) -> None:
    """The web trace groups and labels rows by exactly these keys.

    Without them a CLI-backed turn renders every tool row as the stage name
    ("Responding"), and the model's reasoning disappears: thinking carried no
    ``call_id``, and ``groupTraceEvents`` groups by nothing else.
    """
    backend = _RecordingBackend(
        [
            AgentLoopEvent("thinking", text="pondering"),
            AgentLoopEvent("content", text="let me look"),
            AgentLoopEvent(
                "tool_call",
                name="read_skill",
                data={"args": {"name": "dataviz"}, "id": "tool-1"},
            ),
            AgentLoopEvent(
                "tool_result",
                name="read_skill",
                text="Execute skill: dataviz",
                data={"id": "tool-1", "is_error": True},
            ),
            AgentLoopEvent("thinking", text="that failed"),
            AgentLoopEvent("content", text="answer"),
        ]
    )
    _configure(monkeypatch, backend)
    context = UnifiedContext(session_id="s", user_message="hi", language="en")
    events = await _run_capability_events(context, StreamBus())

    def of(kind: str) -> list[StreamEvent]:
        return [event for event in events if event.type.value == kind]

    progress, thinking, content = of("progress"), of("thinking"), of("content")
    calls, results = of("tool_call"), of("tool_result")

    # Round 1 opens on its first chunk, and every chunk joins it.
    opened = progress[0]
    assert opened.content == "Exploring"
    assert opened.metadata["trace_kind"] == "call_status"
    assert opened.metadata["call_state"] == "running"
    assert opened.metadata["call_kind"] == "agent_loop_round"
    assert opened.metadata["trace_group"] == "stage"
    assert opened.metadata["trace_role"] == "explore"
    assert opened.metadata["phase"] == "responding"
    round_id = str(opened.metadata["call_id"])
    assert round_id.startswith("chat-round-")
    for chunk in (thinking[0], content[0]):
        assert chunk.metadata["call_id"] == round_id
        assert chunk.metadata["call_kind"] == "agent_loop_round"
        assert chunk.metadata["trace_kind"] == "llm_chunk"

    # The tool call ends the round. "round" (not "narration") keeps the prose
    # in the answer bubble: a narration marker would strip it from the message.
    assert progress[1].metadata["call_id"] == round_id
    assert progress[1].metadata["call_state"] == "complete"
    assert progress[1].metadata["call_role"] == "round"
    assert all(event.metadata.get("call_role") != "narration" for event in events)

    # Each tool call is its own row, named in KAGWeb's vocabulary.
    assert calls[0].metadata["call_id"] == "tool-1"
    assert calls[0].metadata["tool_name"] == "read_skill"
    assert calls[0].metadata["call_kind"] == "tool_planning"
    assert calls[0].metadata["trace_group"] == "tool_call"
    assert calls[0].metadata["trace_role"] == "tool"
    assert calls[0].metadata["trace_kind"] == "tool_call"
    assert calls[0].metadata["call_state"] == "running"
    assert calls[0].metadata["args"] == {"name": "dataviz"}
    assert results[0].metadata["call_id"] == "tool-1"
    assert results[0].metadata["tool_name"] == "read_skill"
    assert results[0].metadata["trace_kind"] == "tool_result"
    assert results[0].metadata["call_state"] == "error"
    assert results[0].metadata["is_error"] is True
    # Backend-authoritative duration, recorded between the call and its result.
    assert isinstance(results[0].metadata["elapsed_ms"], int)

    # The pass's last round is the terminal one — the signal the trace settles on.
    assert progress[-2].metadata["call_state"] == "running"
    assert progress[-1].metadata["call_id"] == progress[-2].metadata["call_id"]
    assert progress[-1].metadata["call_role"] == "finish"
    assert content[1].metadata["call_id"] == progress[-2].metadata["call_id"]

    # Narration is part of the answer, exactly as before the change.
    assert context.capability_output.agent_output == "let me look\n\nanswer"


async def test_tool_results_emit_a_truncated_observation_excerpt(monkeypatch) -> None:
    """The collapsed row shows what the tool saw without expanding the result.

    Mechanically derived and capped: a 500-char result becomes a 201-char
    observation (200 + ellipsis) that joins its tool's group; an empty result
    emits nothing, so a chatty backend cannot pad the turn's history.
    """
    backend = _RecordingBackend(
        [
            AgentLoopEvent("tool_call", name="exec", data={"args": {"command": "ls"}, "id": "t1"}),
            AgentLoopEvent("tool_result", name="exec", text="x" * 500, data={"id": "t1"}),
            AgentLoopEvent("tool_call", name="exec", data={"args": {"command": "pwd"}, "id": "t2"}),
            AgentLoopEvent("tool_result", name="exec", text="", data={"id": "t2"}),
        ]
    )
    _configure(monkeypatch, backend)
    context = UnifiedContext(session_id="s", user_message="hi", language="en")
    events = await _run_capability_events(context, StreamBus())

    observations = [event for event in events if event.type.value == "observation"]
    assert len(observations) == 1
    observation = observations[0]
    assert len(observation.content) == 201
    assert observation.content.endswith("…")
    assert observation.metadata["call_id"] == "t1"
    assert observation.metadata["trace_kind"] == "observation"
    assert observation.metadata["trace_group"] == "tool_call"
    assert observation.metadata["tool_name"] == "exec"
    assert observation.metadata["call_state"] == "complete"


async def test_a_result_id_that_names_no_call_pairs_with_the_minted_one(monkeypatch) -> None:
    """A backend may id the result but not the call. The result must attach to
    the call still waiting for one, or the call row stays "running" forever
    while its result opens a second row."""
    backend = _RecordingBackend(
        [
            AgentLoopEvent("tool_call", name="exec", data={"args": {"command": "ls"}}),
            AgentLoopEvent("tool_result", name="exec", text="files", data={"id": "vendor-1"}),
        ]
    )
    _configure(monkeypatch, backend)
    context = UnifiedContext(session_id="s", user_message="hi", language="en")
    events = await _run_capability_events(context, StreamBus())

    calls = [event for event in events if event.type.value == "tool_call"]
    results = [event for event in events if event.type.value == "tool_result"]
    assert calls[0].metadata["call_id"] == results[0].metadata["call_id"]
    assert results[0].metadata["call_state"] == "complete"
    assert results[0].metadata["tool_name"] == "exec"


async def test_id_less_tool_calls_are_minted_and_paired(monkeypatch) -> None:
    """Custom backends never send a tool id; the row must still form and pair."""
    backend = _RecordingBackend(
        [
            AgentLoopEvent("tool_call", name="exec", data={"args": {"command": "ls"}}),
            AgentLoopEvent("tool_call", name="exec", data={"args": {"command": "pwd"}}),
            AgentLoopEvent("tool_result", name="exec", text="files"),
            AgentLoopEvent("tool_result", name="exec", text="/tmp"),
        ]
    )
    _configure(monkeypatch, backend)
    context = UnifiedContext(session_id="s", user_message="hi", language="en")
    events = await _run_capability_events(context, StreamBus())

    calls = [event for event in events if event.type.value == "tool_call"]
    results = [event for event in events if event.type.value == "tool_result"]
    assert [call.metadata["call_id"] for call in calls] == [
        result.metadata["call_id"] for result in results
    ]
    assert len({call.metadata["call_id"] for call in calls}) == 2
    assert all(str(call.metadata["call_id"]).startswith("chat-tool-") for call in calls)
    assert all(result.metadata["tool_name"] == "exec" for result in results)


async def test_configured_workdir_inside_roots_is_used(monkeypatch, tmp_path) -> None:
    backend = _RecordingBackend([AgentLoopEvent("content", text="ok")])
    monkeypatch.setattr(
        "kagweb.capabilities.chat.capability.get_agent_loop_settings",
        lambda: {
            "backend": "recording",
            "session_workspace": False,
            "workdir": "data/user/app",
            "allowed_workdir_roots": ["data/user"],
        },
    )
    monkeypatch.setattr(
        "kagweb.capabilities.chat.capability.build_agent_loop_backend",
        lambda settings: backend,
    )
    monkeypatch.setattr(
        "kagweb.multi_user.paths.get_admin_path_service",
        lambda: SimpleNamespace(project_root=tmp_path),
    )
    context = UnifiedContext(session_id="s", user_message="hi", language="en")
    events = await _run_capability(context, StreamBus())

    assert backend.requests[0].workdir == str((tmp_path / "data" / "user" / "app").resolve())
    # The only progress lines are the round markers — no workdir warning.
    assert {content for kind, content in events if kind == "progress"} <= {"Exploring", ""}


async def test_workdir_outside_roots_falls_back_and_warns(monkeypatch, tmp_path) -> None:
    """A path the allowlist forbids must not reach the subprocess, and the
    trace has to say why instead of silently running elsewhere."""
    backend = _RecordingBackend([AgentLoopEvent("content", text="ok")])
    monkeypatch.setattr(
        "kagweb.capabilities.chat.capability.get_agent_loop_settings",
        lambda: {
            "backend": "recording",
            "session_workspace": False,
            "workdir": "elsewhere",
            "allowed_workdir_roots": ["data/user"],
        },
    )
    monkeypatch.setattr(
        "kagweb.capabilities.chat.capability.build_agent_loop_backend",
        lambda settings: backend,
    )
    monkeypatch.setattr(
        "kagweb.multi_user.paths.get_admin_path_service",
        lambda: SimpleNamespace(project_root=tmp_path),
    )
    context = UnifiedContext(session_id="s", user_message="hi", language="en")
    events = await _run_capability(context, StreamBus())

    assert backend.requests[0].workdir == ""
    assert any(kind == "progress" for kind, _ in events)


async def test_empty_allowlist_refuses_every_workdir(monkeypatch, tmp_path) -> None:
    """Clearing the roots list is how an operator forbids per-profile workdirs."""
    backend = _RecordingBackend([AgentLoopEvent("content", text="ok")])
    monkeypatch.setattr(
        "kagweb.capabilities.chat.capability.get_agent_loop_settings",
        lambda: {
            "backend": "recording",
            "session_workspace": False,
            "workdir": "data/user/app",
            "allowed_workdir_roots": [],
        },
    )
    monkeypatch.setattr(
        "kagweb.capabilities.chat.capability.build_agent_loop_backend",
        lambda settings: backend,
    )
    monkeypatch.setattr(
        "kagweb.multi_user.paths.get_admin_path_service",
        lambda: SimpleNamespace(project_root=tmp_path),
    )
    context = UnifiedContext(session_id="s", user_message="hi", language="en")
    events = await _run_capability(context, StreamBus())

    assert backend.requests[0].workdir == ""
    assert any(kind == "progress" for kind, _ in events)


async def test_http_family_ignores_the_workdir_setting(monkeypatch, tmp_path) -> None:
    """The HTTP family runs in the operator's own service: a configured workdir
    must not be resolved, created, or warned about on its behalf."""

    class _HttpishBackend(_RecordingBackend):
        uses_workdir = False

    backend = _HttpishBackend([AgentLoopEvent("content", text="ok")])
    monkeypatch.setattr(
        "kagweb.capabilities.chat.capability.get_agent_loop_settings",
        lambda: {
            "backend": "recording",
            "session_workspace": False,
            "workdir": "data/user/app",
            "allowed_workdir_roots": ["data/user"],
        },
    )
    monkeypatch.setattr(
        "kagweb.capabilities.chat.capability.build_agent_loop_backend",
        lambda settings: backend,
    )
    monkeypatch.setattr(
        "kagweb.multi_user.paths.get_admin_path_service",
        lambda: SimpleNamespace(project_root=tmp_path),
    )
    context = UnifiedContext(session_id="s", user_message="hi", language="en")
    events = await _run_capability(context, StreamBus())

    assert backend.requests[0].workdir == ""
    assert {content for kind, content in events if kind == "progress"} <= {"Exploring", ""}
    assert not (tmp_path / "data" / "user" / "app").exists()


async def test_backend_error_fails_the_turn(monkeypatch) -> None:
    from kagweb.services.agent_loop.protocol import AgentLoopError

    class _ExplodingBackend:
        name = "boom"

        async def run(self, request: Any):
            raise AgentLoopError("backend exploded")
            yield  # pragma: no cover - generator marker

    monkeypatch.setattr(
        "kagweb.capabilities.chat.capability.get_agent_loop_settings",
        lambda: {"backend": "boom", "session_workspace": False},
    )
    monkeypatch.setattr(
        "kagweb.capabilities.chat.capability.build_agent_loop_backend",
        lambda settings: _ExplodingBackend(),
    )
    context = UnifiedContext(session_id="s", user_message="hi", language="en")
    with pytest.raises(AgentLoopError, match="backend exploded"):
        await ChatCapability().run(context, StreamBus())


async def test_empty_answer_surfaces_non_terminal_error(monkeypatch) -> None:
    """Backend finishing with zero content: turn completes, trace shows why."""
    backend = _RecordingBackend([AgentLoopEvent("thinking", text="worked hard")])
    monkeypatch.setattr(
        "kagweb.capabilities.chat.capability.get_agent_loop_settings",
        lambda: {"backend": "recording", "session_workspace": False},
    )
    monkeypatch.setattr(
        "kagweb.capabilities.chat.capability.build_agent_loop_backend",
        lambda settings: backend,
    )
    context = UnifiedContext(session_id="s", user_message="hi", language="en")
    events = await _run_capability(context, StreamBus())

    kinds = [kind for kind, _ in events]
    assert kinds == ["progress", "thinking", "progress", "error", "result"]
    from kagweb.services.i18n import t

    assert events[3][1] in {
        t("agent_loop.empty_answer", backend="recording", language="en"),
        t("agent_loop.empty_answer", backend="recording", language="zh"),
    }
    assert context.capability_output.agent_output == ""


async def test_session_workspace_is_created_before_use(tmp_path, monkeypatch) -> None:
    """The per-session workdir must exist before the subprocess spawns."""
    from kagweb.capabilities.chat.capability import _build_request

    class _StubPathService:
        def get_task_workspace(self, feature: str, task_id: str):
            return tmp_path / "ws" / feature / task_id  # deliberately not created

    monkeypatch.setattr("kagweb.services.path_service.get_path_service", lambda: _StubPathService())
    request = _build_request(
        UnifiedContext(session_id="sess-9", user_message="hi"), session_workspace=True
    )
    assert request.workdir == str(tmp_path / "ws" / "chat" / "sess-9")
    assert (tmp_path / "ws" / "chat" / "sess-9").is_dir()


# ---------------------------------------------------------------------------
# Approval requests (control-capable backends)
# ---------------------------------------------------------------------------


class _ControlledBackend(_RecordingBackend):
    """A backend that can park on approvals and record decisions."""

    name = "controlled"
    uses_workdir = False
    supports_control = True

    def __init__(self, events: list[AgentLoopEvent]) -> None:
        super().__init__(events)
        self.decisions: list[tuple[str, str]] = []
        self.clarifications: list[tuple[str, str]] = []

    async def respond_approval(self, request_id: str, choice: str) -> None:
        self.decisions.append((request_id, choice))

    async def respond_clarify(self, request_id: str, answer: str) -> None:
        self.clarifications.append((request_id, answer))


def _approval_event() -> AgentLoopEvent:
    return AgentLoopEvent(
        "approval_request",
        name="shell",
        text="rm -rf build/",
        data={"request_id": "req-1", "choices": ["once", "always", "deny"]},
    )


def _clarify_event() -> AgentLoopEvent:
    return AgentLoopEvent(
        "clarify_request",
        text="Which database should I target?",
        data={"request_id": "clr-1", "choices": ["postgres", "sqlite"]},
    )


def _context_with_waiter(waiter) -> UnifiedContext:
    from kagweb.core.context import TurnRuntimeContext

    return UnifiedContext(
        session_id="sess-1",
        user_message="go",
        language="en",
        runtime=TurnRuntimeContext(turn_id="t1", wait_for_user_reply=waiter),
    )


async def _collect_full(context: UnifiedContext, bus: StreamBus) -> list[Any]:
    collected: list[Any] = []

    async def collect() -> None:
        async for event in bus.subscribe():
            collected.append(event)

    task = asyncio.create_task(collect())
    await ChatCapability().run(context, bus)
    await bus.close()
    await task
    return collected


async def test_approval_parks_and_forwards_label_decision(monkeypatch) -> None:
    backend = _ControlledBackend([_approval_event(), AgentLoopEvent("content", text="ok")])
    monkeypatch.setattr(
        "kagweb.capabilities.chat.capability.get_agent_loop_settings",
        lambda: {"backend": "controlled", "session_workspace": False},
    )
    monkeypatch.setattr(
        "kagweb.capabilities.chat.capability.build_agent_loop_backend",
        lambda settings: backend,
    )

    async def waiter():
        # The CLI resolves a "1"-style pick to the printed option *label*.
        return {"answers": [{"questionId": "approval", "text": "Allow once"}]}

    events = await _collect_full(_context_with_waiter(waiter), StreamBus())

    assert backend.decisions == [("req-1", "once")]
    kinds = [event.type.value for event in events]
    # The card, then the decision marker, then the resumed round the bridge
    # opens for the answer and settles as the turn's final one.
    assert kinds == [
        "tool_call",
        "tool_result",
        "progress",
        "progress",
        "content",
        "progress",
        "result",
    ]
    card = events[0]
    assert str((card.metadata or {}).get("call_id", "")).startswith("approval-")
    call_args = card.metadata.get("args") if isinstance(card.metadata, dict) else None
    assert isinstance(call_args, dict) and call_args["questions"][0]["id"] == "approval"
    result_event = events[1]
    ask = (result_event.metadata or {}).get("tool_metadata", {}).get("ask_user")
    assert ask and ask["questions"][0]["options"][0]["value"] == "once"
    decision = events[2]
    assert (decision.metadata or {}).get("approval") == {
        "request_id": "req-1",
        "tool": "shell",
        "decision": "once",
    }
    assert context_answer(events) == "ok"


async def test_approval_matches_raw_value_and_denies_unknown(monkeypatch) -> None:
    decisions_seen: list[list[tuple[str, str]]] = []

    async def scenario(reply_text: str) -> None:
        backend = _ControlledBackend([_approval_event(), AgentLoopEvent("content", text="ok")])
        monkeypatch.setattr(
            "kagweb.capabilities.chat.capability.build_agent_loop_backend",
            lambda settings: backend,
        )
        monkeypatch.setattr(
            "kagweb.capabilities.chat.capability.get_agent_loop_settings",
            lambda: {"backend": "controlled", "session_workspace": False},
        )

        async def waiter():
            return {"answers": [{"questionId": "approval", "text": reply_text}]}

        await _collect_full(_context_with_waiter(waiter), StreamBus())
        decisions_seen.append(backend.decisions)

    await scenario("always")  # raw value matches
    await scenario("banana")  # unknown answer falls back to deny
    assert decisions_seen == [[("req-1", "always")], [("req-1", "deny")]]


async def test_approval_timeout_uses_policy_default(monkeypatch) -> None:
    import kagweb.capabilities.chat.capability as cap

    backend = _ControlledBackend([_approval_event(), AgentLoopEvent("content", text="ok")])
    monkeypatch.setattr(
        "kagweb.capabilities.chat.capability.get_agent_loop_settings",
        lambda: {"backend": "controlled", "session_workspace": False},
    )
    monkeypatch.setattr(
        "kagweb.capabilities.chat.capability.build_agent_loop_backend",
        lambda settings: backend,
    )
    monkeypatch.setattr(cap, "_approval_timeout", lambda profile: 0.05)

    async def waiter():
        await asyncio.sleep(5)  # outlives the (shortened) park budget
        return {"answers": [{"questionId": "approval", "text": "always"}]}

    events = await _collect_full(_context_with_waiter(waiter), StreamBus())
    assert backend.decisions == [("req-1", "deny")]
    assert context_answer(events) == "ok"


async def test_approval_without_runtime_context_denies(monkeypatch) -> None:
    """Headless entry points never park: the policy answers immediately."""
    backend = _ControlledBackend([_approval_event(), AgentLoopEvent("content", text="ok")])
    monkeypatch.setattr(
        "kagweb.capabilities.chat.capability.get_agent_loop_settings",
        lambda: {"backend": "controlled", "session_workspace": False},
    )
    monkeypatch.setattr(
        "kagweb.capabilities.chat.capability.build_agent_loop_backend",
        lambda settings: backend,
    )
    context = UnifiedContext(session_id="s", user_message="go", language="en")
    events = await _collect_full(context, StreamBus())
    assert backend.decisions == [("req-1", "deny")]
    assert context_answer(events) == "ok"


async def test_approval_from_uncontrolled_backend_degrades_to_progress(monkeypatch) -> None:
    backend = _RecordingBackend([_approval_event(), AgentLoopEvent("content", text="ok")])
    monkeypatch.setattr(
        "kagweb.capabilities.chat.capability.get_agent_loop_settings",
        lambda: {"backend": "recording", "session_workspace": False},
    )
    monkeypatch.setattr(
        "kagweb.capabilities.chat.capability.build_agent_loop_backend",
        lambda settings: backend,
    )
    events = await _collect_full(
        UnifiedContext(session_id="s", user_message="go", language="en"), StreamBus()
    )
    kinds = [event.type.value for event in events]
    # A visible note, never a park: the unsupported request degrades to a
    # progress row inside the round the answer then closes.
    assert kinds == [
        "progress",
        "progress",
        "content",
        "progress",
        "result",
    ]
    note = events[0]
    # The request's own preview text wins over the localized fallback, so the
    # operator sees what the agent wanted to run even though it can never be
    # approved here.
    assert str(note.content) == "rm -rf build/"
    assert (note.metadata or {}).get("kind") == "approval_request"


async def test_clarify_parks_and_forwards_the_free_text_answer(monkeypatch) -> None:
    """A clarify asks a question, so its answer is free text and goes to
    ``respond_clarify`` — not the approval's bounded-choice path."""
    backend = _ControlledBackend([_clarify_event(), AgentLoopEvent("content", text="ok")])
    monkeypatch.setattr(
        "kagweb.capabilities.chat.capability.get_agent_loop_settings",
        lambda: {"backend": "controlled", "session_workspace": False},
    )
    monkeypatch.setattr(
        "kagweb.capabilities.chat.capability.build_agent_loop_backend",
        lambda settings: backend,
    )

    async def waiter():
        return {"answers": [{"questionId": "clarify", "text": "postgres please"}]}

    events = await _collect_full(_context_with_waiter(waiter), StreamBus())

    assert backend.clarifications == [("clr-1", "postgres please")]
    assert backend.decisions == []  # never routed through the approval path

    card = next(event for event in events if event.type.value == "tool_call")
    call_args = card.metadata.get("args") if isinstance(card.metadata, dict) else None
    assert isinstance(call_args, dict)
    question = call_args["questions"][0]
    assert question["id"] == "clarify"
    assert question["prompt"] == "Which database should I target?"
    # The agent's own question is the prompt; its suggestions become options.
    assert [option["label"] for option in question["options"]] == ["postgres", "sqlite"]
    # Free text stays available even when choices were offered.
    assert question["allow_free_text"] is True

    resolved = next(event for event in events if (event.metadata or {}).get("ask_user_resolved"))
    assert resolved.metadata["clarify"] == {"request_id": "clr-1", "answer": "postgres please"}
    assert context_answer(events) == "ok"


async def test_clarify_timeout_is_reported_as_skipped_not_invented(monkeypatch) -> None:
    """A clarify has no policy answer: on timeout the turn must tell the agent
    nothing rather than fabricate text it would act on."""
    import kagweb.capabilities.chat.capability as cap

    backend = _ControlledBackend([_clarify_event(), AgentLoopEvent("content", text="ok")])
    monkeypatch.setattr(
        "kagweb.capabilities.chat.capability.get_agent_loop_settings",
        lambda: {"backend": "controlled", "session_workspace": False},
    )
    monkeypatch.setattr(
        "kagweb.capabilities.chat.capability.build_agent_loop_backend",
        lambda settings: backend,
    )
    monkeypatch.setattr(cap, "_approval_timeout", lambda profile: 0.05)

    async def waiter():
        await asyncio.sleep(5)
        return {"answers": [{"questionId": "clarify", "text": "too late"}]}

    events = await _collect_full(_context_with_waiter(waiter), StreamBus())

    assert backend.clarifications == [("clr-1", "")]
    resolved = next(event for event in events if (event.metadata or {}).get("ask_user_resolved"))
    assert resolved.metadata["clarify"]["answer"] == ""
    assert resolved.metadata["answers"] == [{"questionId": "clarify", "text": ""}]


def context_answer(events: list[Any]) -> str:
    contents = [event.content for event in events if event.type.value == "content"]
    return "\n\n".join(contents)


async def test_pass_usage_rides_the_closing_marker_with_its_scope(monkeypatch) -> None:
    """KAGWeb can only attribute tokens to the whole backend pass, so they ride
    the terminal marker — never spread over the rounds inside the loop."""
    backend = _RecordingBackend(
        [
            AgentLoopEvent("thinking", text="pondering"),
            AgentLoopEvent(
                "usage",
                data={"input_tokens": 1200, "output_tokens": 340, "usage_scope": "pass"},
            ),
            AgentLoopEvent("content", text="answer"),
        ]
    )
    _configure(monkeypatch, backend)
    context = UnifiedContext(session_id="s", user_message="hi", language="en")
    events = await _run_capability_events(context, StreamBus())

    markers = [
        event
        for event in events
        if event.type.value == "progress" and event.metadata.get("call_state") == "complete"
    ]
    assert len(markers) == 1
    marker = markers[0]
    assert marker.metadata["prompt_tokens"] == 1200
    assert marker.metadata["completion_tokens"] == 340
    assert marker.metadata["total_tokens"] == 1540
    assert marker.metadata["usage_scope"] == "pass"


async def test_cumulative_usage_is_marked_and_carries_no_total(monkeypatch) -> None:
    backend = _RecordingBackend(
        [
            AgentLoopEvent("content", text="answer"),
            AgentLoopEvent(
                "usage",
                data={"input_tokens": 5000, "output_tokens": 220, "usage_scope": "cumulative"},
            ),
        ]
    )
    _configure(monkeypatch, backend)
    context = UnifiedContext(session_id="s", user_message="hi", language="en")
    events = await _run_capability_events(context, StreamBus())

    marker = [
        event
        for event in events
        if event.type.value == "progress" and event.metadata.get("call_state") == "complete"
    ][0]
    assert marker.metadata["prompt_tokens"] == 5000
    assert marker.metadata["completion_tokens"] == 220
    assert "total_tokens" not in marker.metadata
    assert marker.metadata["usage_scope"] == "cumulative"


async def test_a_missing_counter_is_not_zero_filled(monkeypatch) -> None:
    """A backend that reports only one counter must not be read as a measured
    zero for the other — the pair is simply incomplete."""
    backend = _RecordingBackend(
        [
            AgentLoopEvent("content", text="answer"),
            AgentLoopEvent(
                "usage",
                data={"input_tokens": 3, "output_tokens": None, "usage_scope": "pass"},
            ),
        ]
    )
    _configure(monkeypatch, backend)
    context = UnifiedContext(session_id="s", user_message="hi", language="en")
    events = await _run_capability_events(context, StreamBus())

    marker = [
        event
        for event in events
        if event.type.value == "progress" and event.metadata.get("call_state") == "complete"
    ][0]
    assert marker.metadata["prompt_tokens"] == 3
    assert "completion_tokens" not in marker.metadata
    assert "total_tokens" not in marker.metadata
