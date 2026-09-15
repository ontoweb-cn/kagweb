"""Turn outcome resolution: status, terminal error, and "stopped short".

A backend can end a turn early and still hand back usable text — the model hit
its output ceiling, or the agent ran out of turn budget. That is neither a
failure (the reply is real and worth showing) nor a clean finish (it is a
prefix). Keeping the two apart is what lets the UI say "Done · truncated"
instead of choosing one of two wrong labels.
"""

from __future__ import annotations

from kagweb.core.stream import StreamEvent, StreamEventType
from kagweb.services.session._turn_runtime_shared import _resolve_turn_outcome


def _error(metadata: dict, content: str = "stopped early") -> dict:
    return {
        "type": StreamEventType.ERROR.value,
        "content": content,
        "metadata": metadata,
    }


def _done(status: str) -> StreamEvent:
    return StreamEvent(type=StreamEventType.DONE, metadata={"status": status})


def test_clean_finish_has_no_incomplete_reason() -> None:
    status, error, reason = _resolve_turn_outcome([], _done("completed"))
    assert (status, error, reason) == ("completed", "", "")


def test_non_terminal_stop_reason_marks_the_turn_incomplete() -> None:
    events = [_error({"stop_reason": "max_tokens"})]
    status, error, reason = _resolve_turn_outcome(events, _done("completed"))

    # Still completed: the text is usable and the turn really did end.
    assert status == "completed"
    assert error == ""
    # But the caller can tell it stopped short, and why.
    assert reason == "max_tokens"


def test_terminal_error_still_wins_and_carries_no_reason() -> None:
    events = [_error({"turn_terminal": True, "status": "failed"}, "it broke")]
    status, error, reason = _resolve_turn_outcome(events, None)

    assert status == "failed"
    assert error == "it broke"
    # A failed turn already says so; a truncation badge on top is redundant.
    assert reason == ""


def test_a_failed_turn_discards_an_earlier_stop_reason() -> None:
    """Trailing terminal error outranks an earlier non-terminal marker."""
    events = [
        _error({"stop_reason": "max_tokens"}),
        _error({"turn_terminal": True, "status": "failed"}, "crashed"),
    ]
    status, _error_text, reason = _resolve_turn_outcome(events, None)
    assert status == "failed"
    assert reason == ""


def test_stop_reason_without_a_value_is_not_a_marker() -> None:
    """An error event with no reason must not flip the badge on."""
    events = [_error({"stop_reason": "  "})]
    status, _error_text, reason = _resolve_turn_outcome(events, _done("completed"))
    assert status == "completed"
    assert reason == ""


def test_first_stop_reason_wins() -> None:
    """Scanned newest-first, so the most recent reason is the one reported."""
    events = [
        _error({"stop_reason": "max_tokens"}),
        _error({"stop_reason": "refusal"}),
    ]
    _status, _error_text, reason = _resolve_turn_outcome(events, _done("completed"))
    assert reason == "refusal"
