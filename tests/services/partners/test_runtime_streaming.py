"""When the partner runner considers a reply already delivered live.

The decision gates whether a channel posts the final answer on top of the
segments it already streamed — getting it wrong either duplicates the message
or drops it.
"""

from __future__ import annotations

from kagweb.services.partners.runtime import _already_streamed


def test_joined_agent_loop_rounds_count_as_delivered() -> None:
    # An external agent loop streams one segment per round and the answer is
    # the paragraph join of them, so no single segment matches on its own.
    assert _already_streamed(
        {"round-1": "Let me look.", "round-2": "\n\nThe answer."},
        "Let me look.\n\nThe answer.",
    )


def test_narration_prefix_still_counts_as_delivered() -> None:
    # Native loop: the narration segment streams before the finish, and the
    # finish is the answer.
    assert _already_streamed({"narration": "Searching.", "finish": "\n\nAnswer"}, "Answer")


def test_partial_stream_still_needs_the_full_send() -> None:
    assert not _already_streamed(
        {"round-1": "Let me look."},
        "Let me look.\n\nThe answer.",
    )


def test_empty_answer_is_never_already_delivered() -> None:
    assert not _already_streamed({"round-1": "text"}, "")
