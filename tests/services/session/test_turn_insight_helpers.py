"""Helpers behind the post-turn insight badge.

`_count_llm_rounds` decides whether a turn earns a badge at all (≥2 steps),
`_ordered_tool_names` feeds the judge prompt, and the judge itself must accept
the bare JSON its own prompt asks for.
"""

from __future__ import annotations

import contextlib
from types import SimpleNamespace
from typing import Any

import pytest

from kagweb.services.session.turns.executor import _count_llm_rounds, _ordered_tool_names
from kagweb.services.session.turns.title_service import SessionTitleService

pytestmark = pytest.mark.asyncio


def _ev(kind: str, call_id: str, type_: str = "progress", content: str = "") -> dict[str, Any]:
    return {"type": type_, "content": content, "metadata": {"call_kind": kind, "call_id": call_id}}


def test_count_llm_rounds_counts_distinct_call_ids() -> None:
    events = [
        _ev("agent_loop_round", "r1"),
        _ev("agent_loop_round", "r1"),  # same round, another event
        _ev("llm_final_response", "final"),
        _ev("progress", "x"),  # no call_kind
    ]

    assert _count_llm_rounds(events) == 2


def test_count_llm_rounds_counts_tool_only_passes() -> None:
    """A pass whose assistant message is just a ``tool_use`` block opens no
    round (the bridge mints one only for thinking/prose), so counting rounds
    alone would score a genuinely multi-step turn as 1 and suppress the badge."""
    events = [
        {"type": "tool_call", "content": "exec", "metadata": {"call_id": "t1"}},
        {"type": "tool_result", "content": "ok", "metadata": {"call_id": "t1"}},
        {"type": "tool_call", "content": "read_file", "metadata": {"call_id": "t2"}},
        _ev("agent_loop_round", "r1", "content", "answer"),
    ]

    assert _count_llm_rounds(events) == 3


def test_count_llm_rounds_empty() -> None:
    assert _count_llm_rounds([]) == 0


def test_ordered_tool_names_dedupes_in_order() -> None:
    events = [
        {"type": "tool_call", "content": "read_source"},
        {"type": "tool_call", "content": "rag"},
        {"type": "tool_call", "content": "read_source"},  # duplicate
        {"type": "content", "content": "read_source"},  # not a tool call
    ]

    assert _ordered_tool_names(events) == ["read_source", "rag"]


def test_ordered_tool_names_prefers_the_stamped_name() -> None:
    """The agent-loop bridge stamps `tool_name`; content is the fallback."""
    events = [
        {"type": "tool_call", "content": "exec", "metadata": {"tool_name": "exec"}},
        {"type": "tool_call", "content": "legacy", "metadata": {}},
    ]

    assert _ordered_tool_names(events) == ["exec", "legacy"]


class _Store:
    def __init__(self) -> None:
        self.writes: list[tuple[Any, dict[str, Any]]] = []
        self.accepted = True

    async def update_message_metadata(self, message_id: Any, metadata: dict[str, Any]) -> bool:
        self.writes.append((message_id, metadata))
        return self.accepted


class _Judge(SessionTitleService):
    """The judge with its collaborators stubbed out."""

    def __init__(self) -> None:
        self.store = _Store()
        self.published: list[Any] = []

    async def _publish_live_event(self, execution: Any, event: Any) -> dict[str, Any]:
        self.published.append(event)
        return {}


def _patch_judge(monkeypatch: Any, reply: str) -> _Judge:
    async def _fake_stream(**_kwargs: Any):
        yield reply

    monkeypatch.setattr("kagweb.services.llm.stream", _fake_stream)
    monkeypatch.setattr("kagweb.services.llm.config.has_configured_llm", lambda: True)
    monkeypatch.setattr(
        "kagweb.services.settings.interface_settings.get_turn_insight_enabled",
        lambda: True,
    )
    monkeypatch.setattr(
        "kagweb.services.model_selection.tasks.task_llm_scope", contextlib.nullcontext
    )
    return _Judge()


async def _run_judge(judge: _Judge, reply: str) -> None:  # noqa: ARG001 - reply baked in
    await judge._maybe_generate_turn_insight(
        execution=SimpleNamespace(),
        session_id="s1",
        turn_id="t1",
        assistant_message_id=42,
        ui_language="zh",
        question="q",
        answer="a",
        tool_names=["exec"],
        round_count=2,
    )


async def test_judge_accepts_the_bare_json_its_prompt_asks_for(monkeypatch) -> None:
    """The title path's error guard rejects anything starting with `{`; the
    judge's own prompt demands bare JSON, so reusing it there killed the badge."""
    judge = _patch_judge(monkeypatch, '{"takeaway":"找到了根因","type":"pivot"}')

    await _run_judge(judge, "")

    assert judge.store.writes == [
        (42, {"turn_insight": {"takeaway": "找到了根因", "type": "pivot"}})
    ]
    assert judge.published, "the live badge event was not published"


async def test_judge_accepts_a_fenced_reply_and_clamps_unknown_types(monkeypatch) -> None:
    judge = _patch_judge(monkeypatch, '```json\n{"takeaway":"x","type":"nonsense"}\n```')

    await _run_judge(judge, "")

    assert judge.store.writes[0][1]["turn_insight"]["type"] == "insight"


async def test_judge_ignores_a_non_object_reply(monkeypatch) -> None:
    judge = _patch_judge(monkeypatch, "[1, 2]")

    await _run_judge(judge, "")

    assert judge.store.writes == []
    assert judge.published == []


async def test_judge_does_not_publish_a_badge_it_could_not_store(monkeypatch) -> None:
    """A live badge the reload cannot reproduce is worse than no badge."""
    judge = _patch_judge(monkeypatch, '{"takeaway":"x","type":"insight"}')
    judge.store.accepted = False

    await _run_judge(judge, "")

    assert judge.store.writes and judge.published == []
