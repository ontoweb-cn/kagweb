"""Call-group classification in the session DSL export.

The classification is duplicated (by design) in the web DAG port
(`web/features/chat/dag/aggregate.ts`), and the inline activity trace applies
the same rules — see `web/tests/session-dag.test.ts` for the TypeScript half.
"""

from __future__ import annotations

from typing import Any

from kagweb.services.session.dsl_export import _walk_call_groups


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
