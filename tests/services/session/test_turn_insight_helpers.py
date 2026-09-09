"""Helpers behind the post-turn insight badge.

`_count_llm_rounds` decides whether a turn earns a badge at all (≥2 rounds),
and `_ordered_tool_names` feeds the judge prompt.
"""

from __future__ import annotations

from typing import Any

from kagweb.services.session.turns.executor import _count_llm_rounds, _ordered_tool_names


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
