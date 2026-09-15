"""Tests for learner-facing unicode escape decoding (#973)."""

from __future__ import annotations

import pytest

from kagweb.services.i18n import t
from kagweb.utils.text_display import (
    decode_escaped_unicode_for_display,
    first_line_label,
    untruncated_stem,
)


def test_decodes_dense_non_ascii_runs() -> None:
    escaped = "\\u300c\\u6570\\u5236\\u8f6c\\u6362\\u300d"
    assert decode_escaped_unicode_for_display(escaped) == "「数制转换」"


def test_leaves_short_ascii_runs_alone() -> None:
    text = "A JSON string can encode A as \\u0041."
    assert decode_escaped_unicode_for_display(text) == text


def test_empty_and_plain_text_passthrough() -> None:
    assert decode_escaped_unicode_for_display("") == ""
    assert decode_escaped_unicode_for_display("hello") == "hello"


class TestFirstLineLabel:
    def test_keeps_short_single_line_input_as_is(self) -> None:
        assert first_line_label("shell", limit=80) == "shell"

    def test_takes_the_first_non_empty_line(self) -> None:
        assert first_line_label("\n  \nrm -rf build/\nmore text", limit=80) == "rm -rf build/"

    def test_clamps_long_input_with_an_ellipsis(self) -> None:
        label = first_line_label("x" * 200, limit=80)
        assert len(label) == 80
        assert label.endswith("…")

    def test_returns_empty_for_blank_input(self) -> None:
        assert first_line_label("", limit=80) == ""
        assert first_line_label("   \n  ", limit=80) == ""
        assert first_line_label(None, limit=80) == ""


def test_untruncated_stem_removes_only_a_trailing_ellipsis() -> None:
    assert untruncated_stem("abc…") == "abc"
    assert untruncated_stem("abc") == "abc"
    # A literal ellipsis inside the text is not a truncation marker; the one
    # at the end still is, which is the only shape first_line_label makes.
    assert untruncated_stem("…") == ""


class TestApprovalCopyHasNoRepr:
    """The approval sentence must never ``repr()`` the tool name.

    ``{tool!r}`` renders multi-line commands with visible ``\\n`` and ``\\'``
    escapes and picks a quote style of its own; the templates wrap the name
    in explicit quotes instead.
    """

    @pytest.mark.parametrize(
        ("key", "kwargs"),
        [
            ("agent_loop.approval_prompt", {"tool": "run 'x'\nnext"}),
            ("agent_loop.approval_decision", {"tool": "run 'x'\nnext", "choice": "once"}),
        ],
    )
    def test_no_escapes_leak(self, key: str, kwargs: dict[str, str]) -> None:
        rendered = t(key, language="en", **kwargs)
        assert "\\n" not in rendered
        assert "\\'" not in rendered
        # The name's real newline stays a newline rather than becoming text.
        assert "\n" in rendered
