"""ChatCapability: shell-stub fallback vs agent-loop delegation."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from kagweb.capabilities.chat.capability import ChatCapability
from kagweb.core.context import UnifiedContext
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
        persona_context="Be terse.",
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
        "thinking",
        "content",
        "content",
        "tool_call",
        "tool_result",
        "error",
        "result",
    ]
    # Grounding blocks are folded into the prompt ahead of the user message.
    request = backend.requests[0]
    assert request.prompt.startswith("Be terse.")
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
    assert all(kind != "progress" for kind, _ in events)


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
    assert all(kind != "progress" for kind, _ in events)
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
    assert kinds == ["thinking", "error", "result"]
    from kagweb.services.i18n import t

    assert events[1][1] in {
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

    async def respond_approval(self, request_id: str, choice: str) -> None:
        self.decisions.append((request_id, choice))


def _approval_event() -> AgentLoopEvent:
    return AgentLoopEvent(
        "approval_request",
        name="shell",
        text="rm -rf build/",
        data={"request_id": "req-1", "choices": ["once", "always", "deny"]},
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
    assert kinds == ["tool_call", "tool_result", "progress", "content", "result"]
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
    assert kinds == ["progress", "content", "result"]  # visible note, never a park


def context_answer(events: list[Any]) -> str:
    contents = [event.content for event in events if event.type.value == "content"]
    return "\n\n".join(contents)
