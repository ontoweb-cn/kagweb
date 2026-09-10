"""Helpers behind the post-turn insight badge.

`_count_llm_rounds` decides whether a turn earns a badge at all (≥2 steps),
`_ordered_tool_names` feeds the judge prompt, and the judge itself must accept
the bare JSON its own prompt asks for.

The judge is also reached through the composed `TurnRuntimeManager`, where the
method lives on a *different* mixin (`SessionTitleService`) than the caller
(`TurnExecutor`). Nothing in the test suite exercised that path, and a static
checker cannot see across the mixins either — so one test below drives a real
composed manager end to end.
"""

from __future__ import annotations

import contextlib
from types import SimpleNamespace
from typing import Any

import pytest

from kagweb.services.session.sqlite_store import SQLiteSessionStore
from kagweb.services.session.turn_runtime import TurnRuntimeManager, _TurnExecution
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


def test_live_subscriber_accepts_its_queue() -> None:
    """Regression: the class carried a bare ``queue`` annotation with no
    ``@dataclass``, so ``_LiveSubscriber(queue=...)`` raised TypeError. That
    line sits in ``TurnRuntimeManager.subscribe_turn``, which is not currently
    reached by the WebSocket/CLI/SDK adapters (they go through
    ``TurnApplicationService.subscribe_turn``, which reads the coordinator and
    store directly) — so this was a latent trap rather than an outage. It made
    that method unusable for anyone who wires it up, and an annotated field
    does not create an initialiser, so this pins the decorator in place."""
    import asyncio

    from kagweb.services.session._turn_runtime_shared import _LiveSubscriber

    queue: asyncio.Queue[Any] = asyncio.Queue()
    subscriber = _LiveSubscriber(queue=queue)

    assert subscriber.queue is queue


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


async def test_composed_manager_runs_the_post_turn_judge_end_to_end(monkeypatch, tmp_path) -> None:
    """The caller is ``TurnExecutor``; the method lives on
    ``SessionTitleService``. Only ``TurnRuntimeManager`` mixes both in, so a
    standalone ``TurnExecutor`` would raise AttributeError here — and the
    caller gathers with ``return_exceptions=True``, so that failure would be
    swallowed at debug level and the badge would simply never appear. Drive a
    real composed manager against a real store to prove the wiring resolves,
    persists, and publishes.
    """

    async def _fake_stream(**_kwargs: Any):
        yield '{"takeaway":"找到了根因","type":"pivot"}'

    monkeypatch.setattr("kagweb.services.llm.stream", _fake_stream)
    monkeypatch.setattr("kagweb.services.llm.config.has_configured_llm", lambda: True)
    monkeypatch.setattr(
        "kagweb.services.settings.interface_settings.get_turn_insight_enabled",
        lambda: True,
    )
    monkeypatch.setattr(
        "kagweb.services.model_selection.tasks.task_llm_scope", contextlib.nullcontext
    )

    store = SQLiteSessionStore(tmp_path / "chat_history.db")
    manager = TurnRuntimeManager(store)
    session = await store.ensure_session(None)
    message_id = await store.add_message(
        session_id=session["id"], role="assistant", content="the answer"
    )
    execution = _TurnExecution(
        turn_id="turn-1",
        session_id=session["id"],
        capability="chat",
        payload={},
    )
    manager._executions["turn-1"] = execution

    # The executor's call site: an attribute lookup on the composed class.
    await manager._maybe_generate_turn_insight(
        execution=execution,
        session_id=session["id"],
        turn_id="turn-1",
        assistant_message_id=message_id,
        ui_language="zh",
        question="why is it slow?",
        answer="the answer",
        tool_names=["exec"],
        round_count=2,
    )

    messages = await store.get_messages(session["id"])
    stored = (messages[-1].get("metadata") or {}).get("turn_insight")
    assert stored == {"takeaway": "找到了根因", "type": "pivot"}
    badges = [
        event
        for event in execution.events
        if (event.get("metadata") or {}).get("trace_kind") == "turn_insight"
    ]
    assert badges, "the live badge event was not published"
    assert badges[0]["metadata"]["assistant_message_id"] == message_id
