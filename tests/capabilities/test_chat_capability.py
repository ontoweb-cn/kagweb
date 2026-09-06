"""ChatCapability: shell-stub fallback vs agent-loop delegation."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from kagweb.capabilities.chat.capability import ChatCapability
from kagweb.core.context import UnifiedContext
from kagweb.runtime.stream_bus import StreamBus
from kagweb.services.agent_loop.protocol import AgentLoopEvent

pytestmark = pytest.mark.asyncio


class _RecordingBackend:
    name = "recording"

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
    monkeypatch.setattr("kagweb.capabilities.chat.capability.get_agent_loop_backend", lambda: None)
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
        "kagweb.capabilities.chat.capability.get_agent_loop_backend", lambda: backend
    )
    monkeypatch.setattr(
        "kagweb.services.agent_loop.settings.get_agent_loop_settings",
        lambda: {"backend": "recording", "session_workspace": False},
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
    # The answer is the concatenated content; the RESULT envelope names the
    # backend that produced it.
    assert context.capability_output.agent_output == "ab"
    assert context.capability_output.answer_published is True
    result = events[-1]
    assert result[0] == "result"


async def test_backend_error_fails_the_turn(monkeypatch) -> None:
    from kagweb.services.agent_loop.protocol import AgentLoopError

    class _ExplodingBackend:
        name = "boom"

        async def run(self, request: Any):
            raise AgentLoopError("backend exploded")
            yield  # pragma: no cover - generator marker

    monkeypatch.setattr(
        "kagweb.capabilities.chat.capability.get_agent_loop_backend",
        lambda: _ExplodingBackend(),
    )
    monkeypatch.setattr(
        "kagweb.services.agent_loop.settings.get_agent_loop_settings",
        lambda: {"backend": "boom", "session_workspace": False},
    )
    context = UnifiedContext(session_id="s", user_message="hi", language="en")
    with pytest.raises(AgentLoopError, match="backend exploded"):
        await ChatCapability().run(context, StreamBus())
