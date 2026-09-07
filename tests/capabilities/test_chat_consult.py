"""The consult orchestration: primary loop asks, secondary profiles answer,
the turn converges — DeepMentor's consult_subagent adapted to KAGWeb's
delegated-turn architecture (multi-pass + tail-directive protocol)."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from kagweb.capabilities.chat.capability import ChatCapability
from kagweb.core.context import UnifiedContext
from kagweb.runtime.stream_bus import StreamBus
from kagweb.services.agent_loop.consult import (
    consult_manifest,
    parse_consult_directive,
    strip_consult_directive,
)
from kagweb.services.agent_loop.protocol import AgentLoopEvent

pytestmark = pytest.mark.asyncio


class _ScriptedBackend:
    """Serves one scripted event list per pass."""

    name = "scripted"

    def __init__(self, passes: list[list[AgentLoopEvent]]) -> None:
        self.passes = list(passes)
        self.requests: list[Any] = []

    async def run(self, request: Any):
        self.requests.append(request)
        events = self.passes.pop(0)
        for event in events:
            yield event


def _directive_answer(agent: str, question: str) -> str:
    return (
        f"```consult\n"
        f"{json.dumps({'agent': agent, 'question': question})}\n"
        f"```"
    )


def _settings(
    consult_profiles: list[dict[str, Any]],
    *,
    budget: int = 3,
    primary_id: str = "primary",
) -> dict[str, Any]:
    return {
        "version": 2,
        "primary": primary_id,
        "consult_budget": budget,
        "profiles": [
            {
                "id": primary_id,
                "name": "Primary",
                "preset": "intellect",
                "enabled": True,
                "url": "http://localhost:8083",
                "session_workspace": False,
            },
            *consult_profiles,
        ],
    }


async def _run(context: UnifiedContext, bus: StreamBus):
    events: list[Any] = []

    async def collect() -> None:
        async for event in bus.subscribe():
            events.append(event)

    task = asyncio.create_task(collect())
    await ChatCapability().run(context, bus)
    await bus.close()
    await task
    return events


def _of(events: list[Any], kind: str) -> list[Any]:
    return [event for event in events if event.type.value == kind]


async def test_directive_round_trip_functions() -> None:
    answer = "checking.\n" + _directive_answer("hermes", "q?")
    assert parse_consult_directive(answer) == {"agent": "hermes", "question": "q?"}
    assert strip_consult_directive(answer) == "checking."
    # A block mid-answer never triggers a consult.
    assert parse_consult_directive("```consult\n{}\n```\nbut then I kept going") is None


async def test_consult_flow_runs_secondary_and_converges(monkeypatch) -> None:
    primary = _ScriptedBackend(
        [
            [AgentLoopEvent("content", text=_directive_answer("hermes", "summarize X"))],
            [AgentLoopEvent("content", text="Final answer thanks to the consult.")],
        ]
    )
    consult_backend = _ScriptedBackend(
        [[AgentLoopEvent("content", text="X summarized by hermes.")]]
    )
    settings = _settings(
        [
            {
                "id": "h1",
                "name": "HERMES",
                "preset": "hermes",
                "enabled": True,
                "consult_enabled": True,
                "url": "https://hermes.example",
            }
        ]
    )
    monkeypatch.setattr(
        "kagweb.capabilities.chat.capability.get_agent_loop_settings",
        lambda: settings,
    )
    monkeypatch.setattr(
        "kagweb.capabilities.chat.capability.build_agent_loop_backend",
        lambda profile: (
            primary if profile.get("id") == "primary" else consult_backend
        ),
    )

    context = UnifiedContext(session_id="chat-1", user_message="what about X?", language="en")
    events = await _run(context, StreamBus())

    # The consult exchange is visible as consult_agent tool call + result.
    tool_calls = _of(events, "tool_call")
    assert [event.content for event in tool_calls] == ["consult_agent"]
    assert tool_calls[0].metadata["args"]["question"] == "summarize X"
    assert tool_calls[0].metadata["agent_loop_consult"] == "h1"
    tool_results = _of(events, "tool_result")
    assert tool_results[-1].content == "X summarized by hermes."

    # The consult ran under a stable ::consult:: session id.
    assert consult_backend.requests[0].session_id == "chat-1::consult::h1"

    # The second primary pass received the consult result in history.
    second = primary.requests[1]
    assert second.history[-1]["content"] == "[consult:HERMES]\nX summarized by hermes."
    assert second.history[-2]["role"] == "assistant"

    # The final answer is the second pass's, and the manifest advertised the
    # consultable agent on the first pass.
    assert "manifest listed" or True
    assert "- h1 (HERMES): hermes" in primary.requests[0].prompt
    assert context.capability_output.agent_output == "Final answer thanks to the consult."
    result = _of(events, "result")[-1]
    assert result.metadata["agent_loop"]["consults"] == 1


async def test_consult_budget_is_enforced(monkeypatch) -> None:
    directive = _directive_answer("hermes", "again?")
    primary = _ScriptedBackend(
        [
            [AgentLoopEvent("content", text=directive)],
            [AgentLoopEvent("content", text=directive)],
        ]
    )
    consult_backend = _ScriptedBackend(
        [
            [AgentLoopEvent("content", text="one")],
            [AgentLoopEvent("content", text="two")],
        ]
    )
    settings = _settings(
        [
            {
                "id": "h1",
                "name": "HERMES",
                "preset": "hermes",
                "enabled": True,
                "consult_enabled": True,
                "url": "https://h",
            }
        ],
        budget=1,
    )
    monkeypatch.setattr(
        "kagweb.capabilities.chat.capability.get_agent_loop_settings",
        lambda: settings,
    )
    monkeypatch.setattr(
        "kagweb.capabilities.chat.capability.build_agent_loop_backend",
        lambda profile: primary if profile.get("id") == "primary" else consult_backend,
    )

    context = UnifiedContext(session_id="s", user_message="q", language="en")
    events = await _run(context, StreamBus())

    # One consult only — the second directive answer lands as the final
    # answer (minus its directive block when it is pure directive).
    assert len(consult_backend.requests) == 1
    result = _of(events, "result")[-1]
    assert result.metadata["agent_loop"]["consults"] == 1


async def test_disabled_or_non_consult_profiles_are_not_advertised(monkeypatch) -> None:
    primary = _ScriptedBackend([[AgentLoopEvent("content", text="plain answer")]])
    settings = _settings(
        [
            {
                "id": "off",
                "name": "Off",
                "preset": "hermes",
                "enabled": False,
                "url": "https://h",
            },
            {
                "id": "noprimary",
                "name": "NoConsult",
                "preset": "agentscope",
                "enabled": True,
                "consult_enabled": False,
                "url": "https://a",
            },
        ]
    )
    monkeypatch.setattr(
        "kagweb.capabilities.chat.capability.get_agent_loop_settings",
        lambda: settings,
    )
    monkeypatch.setattr(
        "kagweb.capabilities.chat.capability.build_agent_loop_backend",
        lambda profile: primary,
    )

    context = UnifiedContext(session_id="s", user_message="q", language="en")
    events = await _run(context, StreamBus())

    # No manifest, no consult tool events, single pass.
    assert "<agent-consultation>" not in primary.requests[0].prompt
    assert _of(events, "tool_call") == []
    assert context.capability_output.agent_output == "plain answer"


async def test_unknown_agent_reference_answ_directly(monkeypatch) -> None:
    primary = _ScriptedBackend(
        [
            [AgentLoopEvent("content", text="hmm\n" + _directive_answer("nobody", "q"))],
        ]
    )
    settings = _settings(
        [
            {
                "id": "h1",
                "name": "HERMES",
                "preset": "hermes",
                "enabled": True,
                "consult_enabled": True,
                "url": "https://h",
            }
        ]
    )
    monkeypatch.setattr(
        "kagweb.capabilities.chat.capability.get_agent_loop_settings",
        lambda: settings,
    )
    monkeypatch.setattr(
        "kagweb.capabilities.chat.capability.build_agent_loop_backend",
        lambda profile: primary,
    )

    context = UnifiedContext(session_id="s", user_message="q", language="en")
    events = await _run(context, StreamBus())

    # The unknown reference degrades gracefully: a progress note, the
    # directive stripped, and the spoken answer still published.
    assert any("nobody" in event.content for event in _of(events, "progress"))
    assert context.capability_output.agent_output == "hmm"


async def test_consult_failure_degrades_to_note(monkeypatch) -> None:
    from kagweb.services.agent_loop.protocol import AgentLoopError

    class _ExplodingConsult:
        name = "boom"

        async def run(self, request: Any):
            yield AgentLoopEvent("content", text="partial…")
            raise AgentLoopError("consult backend exploded")

    primary = _ScriptedBackend(
        [
            [AgentLoopEvent("content", text=_directive_answer("h1", "q"))],
            [AgentLoopEvent("content", text="recovered answer")],
        ]
    )
    settings = _settings(
        [
            {
                "id": "h1",
                "name": "HERMES",
                "preset": "hermes",
                "enabled": True,
                "consult_enabled": True,
                "url": "https://h",
            }
        ]
    )
    monkeypatch.setattr(
        "kagweb.capabilities.chat.capability.get_agent_loop_settings",
        lambda: settings,
    )
    monkeypatch.setattr(
        "kagweb.capabilities.chat.capability.build_agent_loop_backend",
        lambda profile: primary if profile.get("id") == "primary" else _ExplodingConsult(),
    )

    context = UnifiedContext(session_id="s", user_message="q", language="en")
    events = await _run(context, StreamBus())

    # The turn does NOT fail: the error is surfaced, the primary recovers.
    assert any("exploded" in event.content for event in _of(events, "error"))
    assert context.capability_output.agent_output == "recovered answer"


def test_manifest_lists_profiles_and_budget() -> None:
    text = consult_manifest(
        [
            {"id": "h1", "name": "HERMES", "preset": "hermes"},
            {"id": "a1", "name": "AgentScope", "preset": "agentscope"},
        ],
        budget=2,
        language="en",
    )
    assert "- h1 (HERMES): hermes" in text
    assert "- a1 (AgentScope): agentscope" in text
    assert "At most 2 consultation(s) this turn" in text
