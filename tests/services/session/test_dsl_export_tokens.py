"""Duration and token extraction for the session DSL export.

Mirrors `web/tests/session-dag.test.ts` and `aggregate.ts`: both sides read the
same metadata keys, and the parity fixture locks the end-to-end shape. These
cases pin the branches the fixture does not reach.
"""

from __future__ import annotations

from typing import Any

from kagweb.services.session.dsl_export import (
    _extract_duration_ms,
    _extract_tokens,
    _extract_usage_scope,
)


def event(metadata: dict[str, Any], timestamp: float = 1) -> dict[str, Any]:
    return {"type": "progress", "content": "", "metadata": metadata, "timestamp": timestamp}


def test_elapsed_ms_beats_the_timestamp_span() -> None:
    events = [event({}, 1), event({"elapsed_ms": 1500}, 9)]

    assert _extract_duration_ms(events) == 1500


def test_timestamp_span_is_the_fallback() -> None:
    assert _extract_duration_ms([event({}, 1), event({}, 4)]) == 3
    assert _extract_duration_ms([event({}, 4), event({}, 4)]) is None
    assert _extract_duration_ms([]) is None


def test_pass_scope_tokens_carry_a_total() -> None:
    events = [
        event(
            {
                "prompt_tokens": 1200,
                "completion_tokens": 340,
                "usage_scope": "pass",
            }
        )
    ]

    assert _extract_tokens(events) == {"prompt": 1200, "completion": 340, "total": 1540}
    assert _extract_usage_scope(events) == "pass"


def test_reported_total_wins_over_the_sum() -> None:
    events = [event({"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 99})]

    assert _extract_tokens(events) == {"prompt": 10, "completion": 5, "total": 99}


def test_cumulative_scope_never_synthesizes_a_total() -> None:
    events = [event({"prompt_tokens": 5000, "completion_tokens": 220, "usage_scope": "cumulative"})]

    assert _extract_tokens(events) == {"prompt": 5000, "completion": 220}
    assert _extract_usage_scope(events) == "cumulative"


def test_incomplete_counters_yield_no_tokens() -> None:
    assert _extract_tokens([event({"prompt_tokens": 10})]) is None
    assert _extract_tokens([event({"prompt_tokens": True, "completion_tokens": 1})]) is None
    assert _extract_tokens([]) is None
    assert _extract_usage_scope([]) is None
