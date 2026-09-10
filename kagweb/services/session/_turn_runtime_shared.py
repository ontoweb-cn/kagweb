"""
Turn-level runtime manager for unified chat streaming.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass, field
import logging
import re
from typing import TYPE_CHECKING, Any
import unicodedata

from kagweb.core.stream import StreamEvent, StreamEventType
from kagweb.services.llm.utils import clean_thinking_tags
from kagweb.services.session.protocol import SessionStoreProtocol

if TYPE_CHECKING:
    from kagweb.runtime.coordination import TurnLease

logger = logging.getLogger(__name__)

#: ``call_kind`` values that mark one LLM round of the chat loop. Two consumers
#: read this notion and must agree: the persisted-answer capture below, and the
#: turn-insight judge's "is this a multi-round exploration?" gate
#: (``turns/executor.py::_count_llm_rounds``). A future loop integration tagged
#: here is picked up by both.
_LLM_ROUND_CALL_KINDS = frozenset({"llm_final_response", "agent_loop_round"})

# Content call_kinds that make up the persisted answer. Most producers emit
# plain CONTENT events without a ``call_id`` (captured unconditionally); an
# event that DOES carry a call_id is captured only when its call_kind marks
# it as answer text. The two members cover the historic chat agent loop's
# final/round tags and stay so future loop integrations tagging their rounds
# the same way are captured without another change here.
_ANSWER_CONTENT_CALL_KINDS = _LLM_ROUND_CALL_KINDS
_FINAL_TURN_STATUSES = frozenset({"completed", "failed", "cancelled"})


def _should_capture_assistant_content(event: StreamEvent) -> bool:
    if event.type != StreamEventType.CONTENT:
        return False
    metadata = event.metadata or {}
    call_id = metadata.get("call_id")
    if not call_id:
        return True
    return metadata.get("call_kind") in _ANSWER_CONTENT_CALL_KINDS


def _coerce_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "on"}:
            return True
        if normalized in {"false", "0", "no", "off"}:
            return False
    return default


def _resolve_turn_outcome(
    assistant_events: Sequence[dict[str, Any]],
    done_event: StreamEvent | None,
) -> tuple[str, str]:
    """Resolve the persisted turn status and error from the terminal protocol."""
    done_metadata = (done_event.metadata or {}) if done_event is not None else {}
    status = str(done_metadata.get("status") or "completed")
    if status not in _FINAL_TURN_STATUSES:
        status = "completed"

    error = ""
    for event in reversed(assistant_events):
        metadata = event.get("metadata")
        metadata = metadata if isinstance(metadata, dict) else {}
        if event.get("type") != StreamEventType.ERROR.value or not metadata.get("turn_terminal"):
            continue
        terminal_status = str(metadata.get("status") or "failed")
        status = terminal_status if terminal_status in _FINAL_TURN_STATUSES else "failed"
        if status == "completed":
            status = "failed"
        error = str(event.get("content") or "")
        break

    return status, error


_FENCED_CODE_BLOCK_RE = re.compile(r"```[\s\S]*?```")
_INLINE_CODE_SPAN_RE = re.compile(r"`[^`\n]*`")
_MATH_SPAN_RE = re.compile(
    r"\\\[[\s\S]*?\\\]|\\\([\s\S]*?\\\)|\$\$[\s\S]*?\$\$|\$(?!\s)(?:\\.|[^$\n])*?(?<!\s)\$"
)


def _is_escaped(text: str, index: int) -> bool:
    """Whether the character at ``index`` has an odd-length backslash prefix."""
    slash_count = 0
    cursor = index - 1
    while cursor >= 0 and text[cursor] == "\\":
        slash_count += 1
        cursor -= 1
    return slash_count % 2 == 1


def _is_emphasis_normal_character(value: str | None) -> bool:
    return bool(value and (value.isalpha() or value.isnumeric()))


def _is_emphasis_punctuation_or_symbol(value: str | None) -> bool:
    return bool(value and unicodedata.category(value)[0] in {"P", "S"})


def _can_open_emphasis(line: str, index: int, marker: str) -> bool:
    before = line[index - 1] if index else None
    after_index = index + len(marker)
    after = line[after_index] if after_index < len(line) else None
    if after is None or after.isspace():
        return False
    if _is_emphasis_punctuation_or_symbol(after):
        return (
            before is None
            or before.isspace()
            or _is_emphasis_punctuation_or_symbol(before)
            or _is_emphasis_normal_character(before)
        )
    return True


def _can_close_emphasis(line: str, index: int, marker: str) -> bool:
    before = line[index - 1] if index else None
    after_index = index + len(marker)
    after = line[after_index] if after_index < len(line) else None
    if before is None or before.isspace():
        return False
    if _is_emphasis_punctuation_or_symbol(before):
        return (
            after is None
            or after.isspace()
            or _is_emphasis_punctuation_or_symbol(after)
            or _is_emphasis_normal_character(after)
        )
    return True


def _paired_emphasis_delimiters(line: str) -> list[tuple[str, int, int]]:
    """Return only syntactically plausible (marker, left opener, right closer) pairs."""
    openers: dict[str, list[int]] = {"*": [], "**": []}
    pairs: list[tuple[str, int, int]] = []
    cursor = 0
    while cursor < len(line):
        if line[cursor] != "*" or _is_escaped(line, cursor):
            cursor += 1
            continue
        run_end = cursor
        while run_end < len(line) and line[run_end] == "*":
            run_end += 1
        marker = line[cursor:run_end]
        if marker in openers:
            can_open = _can_open_emphasis(line, cursor, marker)
            can_close = _can_close_emphasis(line, cursor, marker)
            if can_close and openers[marker]:
                pairs.append((marker, openers[marker].pop(), cursor))
            elif can_open:
                openers[marker].append(cursor)
        cursor = run_end
    return pairs


def _repair_chinese_emphasis_line(line: str) -> str:
    insertions: set[int] = set()
    for marker, left_opener, right_closer in _paired_emphasis_delimiters(line):
        left_before = line[left_opener - 1] if left_opener else None
        left_inside = (
            line[left_opener + len(marker)] if left_opener + len(marker) < len(line) else None
        )
        right_inside = line[right_closer - 1] if right_closer else None
        right_after_index = right_closer + len(marker)
        right_after = line[right_after_index] if right_after_index < len(line) else None

        left_needs_space = _is_emphasis_normal_character(
            left_before
        ) and _is_emphasis_punctuation_or_symbol(left_inside)
        right_needs_space = _is_emphasis_punctuation_or_symbol(
            right_inside
        ) and _is_emphasis_normal_character(right_after)
        if left_needs_space:
            insertions.add(left_opener)
        if right_needs_space:
            insertions.add(right_after_index)

        # Only mirror a required repair onto the other marker in THIS pair.
        if left_needs_space != right_needs_space:
            if _is_emphasis_normal_character(right_inside) and _is_emphasis_normal_character(
                right_after
            ):
                insertions.add(right_after_index)
            elif _is_emphasis_normal_character(left_before) and _is_emphasis_normal_character(
                left_inside
            ):
                insertions.add(left_opener)

    for index in sorted(insertions, reverse=True):
        line = f"{line[:index]} {line[index:]}"
    return line


def _repair_chinese_emphasis_for_persistence(content: str, language: str) -> str:
    """Normalize CJK Markdown emphasis before the assistant answer is stored.

    中文强调标记（**词语**）两侧紧邻汉字/标点时，部分渲染器不将其识别为
    强调；这里的修复在词边界插入空格。围栏代码、行内码与数学 span 保持
    原样——持久化源码必须与其合法渲染一致。仅对中文输出生效。
    """
    if not content or not str(language or "").lower().startswith("zh"):
        return content
    protected: list[str] = []

    def mask(match: re.Match[str]) -> str:
        protected.append(match.group(0))
        return f"\x00CJK_PROTECTED_{len(protected) - 1}\x00"

    masked = _FENCED_CODE_BLOCK_RE.sub(mask, content)
    masked = _MATH_SPAN_RE.sub(mask, masked)
    masked = _INLINE_CODE_SPAN_RE.sub(mask, masked)
    repaired = "\n".join(_repair_chinese_emphasis_line(line) for line in masked.split("\n"))
    return re.sub(
        r"\x00CJK_PROTECTED_(\d+)\x00",
        lambda match: protected[int(match.group(1))],
        repaired,
    )


def _assemble_persisted_answer(
    content_segments: Sequence[tuple[str | None, str]],
    *,
    language: str,
) -> str:
    """Replay captured content bytes as the persisted answer.

    The CJK emphasis repair runs here, at the single seam where the stored
    answer is finalized: the display layer already repairs at render time
    (``web/lib/markdown-display.ts::repairMalformedStrongEmphasis``), but the
    stored source must be correct for every other consumer (exports, other
    clients, the judge's prompt).
    """
    return _repair_chinese_emphasis_for_persistence(
        clean_thinking_tags("".join(text for _call_id, text in content_segments)),
        language,
    )


def _clip_text(value: str, limit: int = 4000) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "\n...[truncated]"


_TITLE_QUOTE_PAIRS: tuple[tuple[str, str], ...] = (
    ('"', '"'),
    ("'", "'"),
    ("“", "”"),
    ("‘", "’"),
    ("「", "」"),
    ("『", "』"),
    ("`", "`"),
)
_TITLE_PREFIXES: tuple[str, ...] = (
    "Title:",
    "title:",
    "TITLE:",
    "Title-",
    "标题：",
    "标题:",
    "对话标题：",
    "对话标题:",
)
_TITLE_TRAILING_PUNCT = ".。!！?？,，;；、 \t"
_INTERRUPTED_TURN_ERROR = "Turn interrupted by server restart. Please retry your message."

#: Openings that mean the title model reported a failure instead of writing one.
#:
#: ``llm_stream`` surfaces a provider failure as streamed *content*, not as a
#: raised exception, so a bad key yields a perfectly well-formed short string —
#: which the sanitizer then trims and stores as the conversation's name, where
#: it stays forever and follows the session into every list that shows it. The
#: fallback path below (truncate the first user message) already handles "no
#: title"; this is what routes an error into it.
#: Every entry carries its own punctuation or is a word no title starts with.
#: A bare "error " would reject "Error handling in Rust", which is a perfectly
#: good name for a conversation — the guard must be cheaper to pass than a real
#: title is to lose.
_TITLE_ERROR_PREFIXES: tuple[str, ...] = (
    "error:",
    "[error",
    "exception:",
    "traceback",
    "错误：",
    "错误:",
    "请求失败",
    "调用失败",
)


def _looks_like_error_payload(text: str) -> bool:
    """Report whether a generated title is really a failure message.

    Kept deliberately narrow. A real title is a handful of words naming a
    subject; it does not open with an error label and is not a serialised
    object. Anything broader risks discarding a legitimate title — the cost of
    a false negative here is one ugly name, the cost of a false positive is a
    good title silently replaced by a truncated question.
    """
    candidate = text.strip()
    if not candidate:
        return False
    if candidate[0] in "{[":
        return True
    lowered = candidate.lower()
    return any(lowered.startswith(prefix) for prefix in _TITLE_ERROR_PREFIXES)


def _sanitize_session_title(raw: str) -> str:
    """Trim the noise LLMs love to add to short titles.

    Strips model reasoning tags, surrounding quotes, leading "Title:" labels,
    trailing punctuation, and Markdown bold/italic markers. Caps length at
    80 characters so a chatty model can't blow past the sidebar layout.
    """
    text = clean_thinking_tags(raw or "").strip()
    if not text:
        return ""
    text = text.splitlines()[0].strip()
    # Iterate until the text stops shrinking — models often nest the
    # noise (e.g. ``**Title:** "Hello"``) so a single pass leaves
    # leftover wrappers.
    for _ in range(8):
        prev = text
        text = text.lstrip("*_#- \t").rstrip("*_ \t")
        for prefix in _TITLE_PREFIXES:
            if text.startswith(prefix):
                text = text[len(prefix) :].strip()
                break
        for opener, closer in _TITLE_QUOTE_PAIRS:
            if len(text) >= 2 and text.startswith(opener) and text.endswith(closer):
                text = text[len(opener) : len(text) - len(closer)].strip()
                break
        text = text.rstrip(_TITLE_TRAILING_PUNCT)
        if text == prev:
            break
    return text[:80]


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item]


def _llm_selection_dict(value: Any) -> dict[str, str] | None:
    from kagweb.services.model_selection import LLMSelection

    selection = LLMSelection.from_payload(value)
    return selection.to_dict() if selection else None


def _partner_group_references(value: Any) -> list[dict[str, str]]:
    """Normalize the structured home-chat reference contract."""
    if not isinstance(value, list):
        return []
    references: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for raw in value[:20]:
        if not isinstance(raw, dict):
            continue
        group_id = str(raw.get("group_id") or "").strip()[:80]
        session_key = str(raw.get("session_key") or "").strip()[:120]
        key = (group_id, session_key)
        if not all(key) or key in seen:
            continue
        seen.add(key)
        references.append({"group_id": group_id, "session_key": session_key})
    return references


def _request_snapshot_metadata(
    *,
    payload: dict[str, Any],
    content: str,
    capability: str,
    config: dict[str, Any],
    attachments: list[dict[str, Any]],
    history_references: list[Any],
    partner_group_references: list[dict[str, str]],
    persona: str,
    llm_selection: dict[str, str] | None,
) -> dict[str, Any]:
    """Persist the front-end context chips with the user message."""
    snapshot: dict[str, Any] = {
        "content": content,
        "capability": capability,
        "enabledTools": _string_list(payload.get("tools")),
        "language": str(payload.get("language", "en") or "en"),
    }
    if attachments:
        snapshot["attachments"] = attachments
    if config:
        snapshot["config"] = dict(config)
    if history_references:
        snapshot["historyReferences"] = history_references
    if partner_group_references:
        snapshot["partnerGroupReferences"] = partner_group_references
    if persona:
        snapshot["persona"] = persona
    if llm_selection:
        snapshot["llmSelection"] = llm_selection
    return {"request_snapshot": snapshot}


async def _count_branch_user_turns(
    store: SessionStoreProtocol,
    session_id: str,
    leaf_message_id: int | None,
) -> int:
    """Count user messages on the active branch's ancestor chain.

    Used by the chat source inventory to assign ``first_seen_turn`` for
    *fresh* sources (= current turn = past_user_turns + 1). When
    ``leaf_message_id`` is ``None`` (legacy linear append) all messages
    in the session are counted; otherwise we walk the
    ``parent_message_id`` chain so sibling branches don't inflate the
    count. Kept tiny and protocol-only (``get_messages``) so it stays
    compatible with every store backend.
    """
    all_msgs = await store.get_messages(session_id)
    if leaf_message_id is None:
        return sum(1 for m in all_msgs if m.get("role") == "user")
    by_id: dict[int, dict[str, Any]] = {}
    for m in all_msgs:
        mid = m.get("id")
        if mid is not None:
            by_id[int(mid)] = m
    count = 0
    current: int | None = int(leaf_message_id)
    safety = 10_000
    while current is not None and safety > 0:
        m = by_id.get(int(current))
        if m is None:
            break
        if m.get("role") == "user":
            count += 1
        parent = m.get("parent_message_id")
        current = int(parent) if parent is not None else None
        safety -= 1
    return count


def _extract_selection_tutor_context(
    config: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Remove and normalize context for the selected-text side tutor."""
    if not isinstance(config, dict):
        return None
    raw = config.pop("selection_tutor_context", None)
    if not isinstance(raw, dict):
        return None

    selected_text = _clip_text(str(raw.get("selected_text", "") or "").strip())
    if not selected_text:
        return None
    context: dict[str, Any] = {
        "selected_text": selected_text,
        "parent_session_id": str(raw.get("parent_session_id", "") or "").strip(),
    }
    raw_source_message_id = raw.get("source_message_id")
    if isinstance(raw_source_message_id, int) and not isinstance(raw_source_message_id, bool):
        context["source_message_id"] = raw_source_message_id
    elif str(raw_source_message_id or "").strip().isdigit():
        context["source_message_id"] = int(str(raw_source_message_id).strip())

    source_message_text = str(raw.get("source_message_text", "") or "").strip()
    if source_message_text:
        context["source_message_text"] = source_message_text
    source_message_role = str(raw.get("source_message_role", "") or "").strip()
    if source_message_role in {"user", "assistant", "system"}:
        context["source_message_role"] = source_message_role
    return context


def _selection_source_excerpt(
    source_text: str,
    selected_text: str,
    *,
    limit: int = 12_000,
) -> str:
    """Bound a source message while keeping the selected passage in view."""
    text = str(source_text or "").strip()
    if len(text) <= limit:
        return text

    needle = str(selected_text or "").strip()
    selection_start = text.find(needle) if needle else -1
    if selection_start < 0:
        return _clip_text(text, limit=limit)

    before = max(1_000, (limit - len(needle)) // 2)
    start = max(0, selection_start - before)
    end = min(len(text), start + limit)
    start = max(0, end - limit)
    excerpt = text[start:end]
    if start > 0:
        excerpt = "[earlier content omitted]\n" + excerpt
    if end < len(text):
        excerpt += "\n[later content omitted]"
    return excerpt


def _selection_is_grounded(source_text: str, selected_text: str) -> bool:
    """Whether the claimed selection occurs in its containing message."""
    source = str(source_text or "")
    selected = str(selected_text or "").strip()
    if not source or not selected:
        return False
    if selected in source:
        return True
    # Browser selections collapse rendered whitespace while the stored source
    # preserves Markdown/code layout. Permit that representational difference,
    # but never accept text that is absent from the authoritative message.
    normalized_source = " ".join(source.split())
    normalized_selected = " ".join(selected.split())
    return bool(normalized_selected and normalized_selected in normalized_source)


async def _resolve_selection_tutor_context(
    store: Any,
    context: dict[str, Any],
) -> dict[str, Any]:
    """Resolve the selected passage's containing message from its parent chat."""
    resolved = dict(context)
    parent_session_id = str(resolved.get("parent_session_id") or "").strip()
    source_message_id = resolved.get("source_message_id")

    authoritative_source_required = bool(
        parent_session_id and isinstance(source_message_id, int) and source_message_id > 0
    )
    source_message = None
    if authoritative_source_required:
        try:
            path = await store.get_messages_for_context(
                parent_session_id,
                source_message_id,
            )
        except Exception:
            raise ValueError("Could not resolve the selected text's source message") from None
        else:
            source_message = next(
                (
                    message
                    for message in reversed(path)
                    if str(message.get("id")) == str(source_message_id)
                ),
                None,
            )
            if source_message is None:
                raise ValueError("The selected text's source message was not found")
            resolved["source_message_text"] = str(source_message.get("content") or "").strip()
            role = str(source_message.get("role") or "").strip()
            if role in {"user", "assistant", "system"}:
                resolved["source_message_role"] = role

    source_text = str(resolved.get("source_message_text") or "").strip()
    selected_text = str(resolved.get("selected_text") or "")
    if not _selection_is_grounded(source_text, selected_text):
        qualifier = "authoritative " if authoritative_source_required else ""
        raise ValueError(f"Selected text was not found in the {qualifier}source message")
    resolved["source_message_text"] = _selection_source_excerpt(
        source_text,
        selected_text,
    )
    return resolved


def _format_selection_tutor_context(context: dict[str, Any], language: str = "en") -> str:
    selected_text = context.get("selected_text", "").strip()
    parent_session_id = context.get("parent_session_id", "").strip() or "(none)"
    source_message_text = str(context.get("source_message_text") or "").strip()
    source_message_role = str(context.get("source_message_role") or "").strip()
    if str(language or "en").lower().startswith("zh"):
        lines = [
            "你是侧栏中的“小老师”，负责回答学习者对聊天内容的局部追问。",
            "用户精确选中的文字是当前问题的直接指代；原消息上下文只用于解释该选中内容在此处的具体含义。",
            "优先依据原消息中的定义、代码、前后句和符号关系回答，不要把短变量名脱离上下文解释成其他缩写。",
            "不要读取、引用或写入全局记忆；不要把用户问题里的“这个/它/上述内容”解释成记忆系统。",
            "如果先前回答偏离了选中内容，请明确纠正并回到选中内容本身。",
            "回答当前问题时要简明、循序渐进；原消息没有提供的信息不要臆造。",
        ]
        if source_message_text:
            role_label = source_message_role or "unknown"
            lines.extend(
                [
                    "",
                    "[原消息上下文]",
                    f"消息角色：{role_label}",
                    source_message_text,
                    "",
                    "[用户精确选中的内容]",
                    selected_text,
                ]
            )
        else:
            lines.extend(["", "[选中内容]", selected_text])
        lines.extend(["", f"来源会话：{parent_session_id}"])
        return "\n".join(lines).strip()

    lines = [
        "You are the Little Tutor in a sidebar, answering local questions about chat content.",
        "The learner's exact selection is the direct referent of the question; use the containing message only to determine what that selection means here.",
        "Prioritize definitions, code, surrounding sentences, and symbol relationships in the source message. Do not reinterpret a short identifier as an unrelated abbreviation.",
        "Do not read, cite, or write global memory. Never reinterpret words such as 'this', 'it', or 'the above' as referring to the memory system.",
        "If an earlier answer drifted away from the selection, correct it explicitly and return to the selection.",
        "Answer clearly and step by step; do not invent information absent from the source message.",
    ]
    if source_message_text:
        role_label = source_message_role or "unknown"
        lines.extend(
            [
                "",
                "[Containing message]",
                f"Message role: {role_label}",
                source_message_text,
                "",
                "[Learner's exact selection]",
                selected_text,
            ]
        )
    else:
        lines.extend(["", "[Selected passage]", selected_text])
    lines.extend(["", f"Source session: {parent_session_id}"])
    return "\n".join(lines).strip()


def _extract_persist_user_message(config: dict[str, Any] | None) -> bool:
    if not isinstance(config, dict):
        return True
    raw = config.pop("_persist_user_message", True)
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        return raw.strip().lower() not in {"false", "0", "no"}
    return bool(raw)


def _extract_regenerate_flag(config: dict[str, Any] | None) -> bool:
    if not isinstance(config, dict):
        return False
    raw = config.pop("_regenerate", False)
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        return raw.strip().lower() in {"true", "1", "yes"}
    return bool(raw)


@dataclass
class _LiveSubscriber:
    queue: asyncio.Queue[dict[str, Any]]


@dataclass
class _TurnExecution:
    turn_id: str
    session_id: str
    capability: str
    payload: dict[str, Any]
    task: asyncio.Task[None] | None = None
    # True while the turn is parked waiting for a user reply. Such a turn
    # holds its resources but is doing no work, so another turn may take
    # over from it.
    awaiting_user_reply: bool = False
    subscribers: list[_LiveSubscriber] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    next_seq: int = 1
    flush_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    events_persisted: bool = False
    persisted_events: list[dict[str, Any]] = field(default_factory=list)
    events_flushed: bool = False
    lease: TurnLease | None = None
    coordination_task: asyncio.Task[None] | None = None
    lease_lost: bool = False
    shutdown_requested: bool = False
