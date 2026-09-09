"""Call-group classification in the session DSL export.

`_classify_trace_group` mirrors `classifyTraceGroup`
(`web/features/chat/trace/selectors.ts`), which the inline activity trace, the
session DAG and this export all share. The TypeScript half of the rule is
covered by `web/tests/trace-selectors.test.ts` and `web/tests/session-dag.test.ts`;
the cross-language output parity by `tests/services/session/test_dsl_parity.py`.
"""

from __future__ import annotations

from typing import Any

from kagweb.services.session.dsl_export import _classify_trace_group, _walk_call_groups


def _event(event_type: str, content: str = "", **metadata: Any) -> dict[str, Any]:
    return {"type": event_type, "content": content, "metadata": metadata}


def test_untagged_tool_group_is_a_tool_call_node() -> None:
    """A turn persisted before the trace contract carries a call_id and a
    state but no call_kind/trace_group; the inline trace renders it as a tool
    row, so the DSL must not call it a round."""
    groups = _walk_call_groups(
        [
            _event("tool_call", call_id="legacy-1", call_state="running"),
            _event("tool_result", call_id="legacy-1", call_state="complete"),
        ]
    )

    assert [group["kind"] for group in groups] == ["tool_call"]


def test_untagged_reasoning_group_stays_a_round() -> None:
    groups = _walk_call_groups(
        [_event("thinking", content="pondering", call_id="legacy-2", call_state="running")]
    )

    assert [group["kind"] for group in groups] == ["round"]


def test_tool_precedence_beats_retrieve_for_a_shared_call_id() -> None:
    """Retrieval events reusing a tool's call_id must stay in that tool's
    group, so the tool check has to come first."""
    group_events = [
        _event("tool_call", content="rag", call_id="t1", trace_group="tool_call", tool_name="rag"),
        _event("progress", content="searching", call_id="t1", trace_role="retrieve", query="q"),
    ]

    assert _classify_trace_group(group_events) == "tool_call"
    assert (
        _classify_trace_group(
            [
                _event(
                    "progress", content="searching", call_id="r1", trace_role="retrieve", query="q"
                )
            ]
        )
        == "retrieve"
    )


def test_skip_rules_drop_final_absorbed_and_substanceless_groups() -> None:
    assert (
        _classify_trace_group(
            [_event("content", content="Answer", call_id="f1", call_kind="llm_final_response")]
        )
        is None
    )
    assert (
        _classify_trace_group(
            [_event("thinking", content="Draft", call_id="a1", absorbed_into_final=True)]
        )
        is None
    )
    assert _classify_trace_group([_event("thinking", call_id="e1")]) is None


def test_tool_planning_call_kind_is_a_tool_call() -> None:
    """The native loop's shape: a tagged `call_kind` with no `trace_group`."""
    assert (
        _classify_trace_group(
            [_event("tool_call", content="rag", call_id="t1", call_kind="tool_planning")]
        )
        == "tool_call"
    )


def test_marker_types_mirror_the_typescript_rule() -> None:
    """Unvalidated JSON metadata: a marker needs a non-empty string name and a
    finite numeric index. `null` / `true` / `""` / `"2"` are dropped on both
    sides, or the DAG grows a phantom `"null"` subagent the CLI export lacks."""
    groups = _walk_call_groups(
        [
            _event("tool_call", content="go", call_id="t1", trace_group="tool_call"),
            _event("progress", content="x", call_id="t1", subagent_name=None),
            _event("progress", content="x", call_id="t1", subagent_name=True),
            _event("progress", content="x", call_id="t1", subagent_name=""),
            _event("progress", content="x", call_id="t1", subagent_name="a", consult_index=True),
            _event("progress", content="x", call_id="t1", subagent_name="b", consult_index=1.5),
            _event("progress", content="x", call_id="t1", subagent_name="c", consult_index="2"),
            _event("tool_result", content="ok", call_id="t1", trace_group="tool_call"),
        ]
    )

    assert groups[0]["subagents"] == [
        {"name": "a", "consult_index": None},
        {"name": "b", "consult_index": 1.5},
        {"name": "c", "consult_index": None},
    ]


def test_subagent_markers_are_deduplicated_and_tool_only() -> None:
    groups = _walk_call_groups(
        [
            _event("tool_call", content="go", call_id="t1", trace_group="tool_call"),
            _event(
                "progress", content="working", call_id="t1", subagent_name="math", consult_index=1
            ),
            _event(
                "progress", content="again", call_id="t1", subagent_name="math", consult_index=1
            ),
            _event("progress", content="working", call_id="t1", subagent_name="writer"),
            _event("tool_result", content="ok", call_id="t1", trace_group="tool_call"),
        ]
    )

    assert groups[0]["subagents"] == [
        {"name": "math", "consult_index": 1},
        {"name": "writer", "consult_index": None},
    ]

    # A round group never carries subagent markers.
    rounds = _walk_call_groups(
        [
            _event(
                "thinking",
                content="p",
                call_id="r1",
                call_kind="agent_loop_round",
                subagent_name="math",
            )
        ]
    )
    assert rounds[0]["kind"] == "round"
    assert rounds[0]["subagents"] == []
