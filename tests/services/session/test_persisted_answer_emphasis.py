"""The persisted answer's CJK emphasis repair.

Markdown emphasis next to CJK punctuation does not render as emphasis
(CommonMark's flanking rules), so the stored source gets a space at the word
boundary. The display layer repairs the same text at render time; this seam
exists so every other consumer of the stored answer sees valid markdown too.
"""

from __future__ import annotations

from kagweb.services.session._turn_runtime_shared import (
    _assemble_persisted_answer,
    _repair_chinese_emphasis_for_persistence,
)


def test_cjk_emphasis_flanked_by_punctuation_gets_a_space() -> None:
    assert (
        _repair_chinese_emphasis_for_persistence("这是**“重点”**内容", "zh")
        == "这是 **“重点”** 内容"
    )


def test_plain_cjk_emphasis_is_left_alone() -> None:
    text = "这是**重点**内容"
    assert _repair_chinese_emphasis_for_persistence(text, "zh") == text


def test_english_output_is_never_touched() -> None:
    text = 'the **"point"** stands'
    assert _repair_chinese_emphasis_for_persistence(text, "en") == text
    assert _repair_chinese_emphasis_for_persistence(text, "") == text


def test_code_spans_and_math_keep_their_bytes() -> None:
    fenced = "```\n这是**“重点”**内容\n```"
    assert _repair_chinese_emphasis_for_persistence(fenced, "zh") == fenced
    inline = "`这是**“重点”**内容`"
    assert _repair_chinese_emphasis_for_persistence(inline, "zh") == inline
    math = "$$这是**“重点”**内容$$"
    assert _repair_chinese_emphasis_for_persistence(math, "zh") == math


def test_persisted_answer_repairs_only_for_chinese() -> None:
    segments = [(None, "这是**“重点”**内容")]
    assert _assemble_persisted_answer(segments, language="zh-CN") == "这是 **“重点”** 内容"
    assert _assemble_persisted_answer(segments, language="en") == "这是**“重点”**内容"
    # The default stays a pure replay for callers that predate the language.
    assert _assemble_persisted_answer(segments) == "这是**“重点”**内容"
