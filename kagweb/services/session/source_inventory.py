"""Branch-isolated, cumulative source inventory for the chat capability.

The chat pipeline shows the LLM an "Attached Sources" manifest each turn so
it knows what the user has attached in this conversation. Historically
this manifest only listed sources the user attached in the *current* turn —
so the model forgot anything uploaded in earlier turns unless re-attached.

This module materialises the manifest as a **session-cumulative inventory**:

* Sources attached in the current turn (the "fresh" set) get a full preview
  in the manifest.
* Sources attached in *prior* turns on the active branch's ancestor chain
  (the "historical" set) get a compact one-line row: id, name, kind, size,
  and the turn ordinal where they first appeared.

Both sets dedupe by source id; fresh always wins on collision. Branch
isolation is enforced by walking ``parent_message_id`` from the active
branch's leaf, so sibling branches never leak sources into each other.

The output is decoupled from the rest of ``turn_runtime``:

    inventory = await build_inventory(store, ..., fresh_*=...)
    manifest_text = render_manifest(inventory)

The manifest is the primary way sources reach the agent backend: the turn
contract has no attachment field. Rows whose attachment was materialized
into the session workspace (see ``attachment_workspace``) also carry the
file's absolute path, so the agent can read the full contents itself.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
import hashlib
import logging
from typing import Any

from kagweb.services.session.protocol import SessionStoreProtocol

logger = logging.getLogger(__name__)

#: Async batch materializer the executor injects: given the attachment
#: records collected from the lineage, return ``{attachment_id: path}``.
MaterializeCallback = Callable[[list[dict[str, Any]]], Awaitable[dict[str, str]]]

# Per-source text-preview caps. Fresh sources get a meaningful preview so
# the model can answer simple "is this the right one?" questions from the
# manifest alone. Historical sources surface only their identity.
MANIFEST_PREVIEW_CHARS_FRESH = 2000
# Image attachments have no extracted text; they surface in the manifest
# only as a path row (when a workspace copy was materialized).
_IMAGE_MIME_PREFIX = "image/"


@dataclass(frozen=True)
class SourceEntry:
    """One row in the per-turn Attached Sources manifest."""

    sid: str
    kind: str  # attachment | history
    name: str
    full_text: str
    fresh: bool
    # 1-indexed ordinal of the user turn this source first appeared in,
    # within the active branch's lineage. Fresh sources use the **current**
    # turn's ordinal so the manifest can label them consistently.
    first_seen_turn: int
    # Stable store id + workspace copy path. Empty for sources with no file
    # behind them (history-session transcripts) and for copies that could
    # not be materialized.
    attachment_id: str = ""
    path: str = ""

    @property
    def char_count(self) -> int:
        return len(self.full_text)


@dataclass
class SourceInventory:
    """Ordered set of ``SourceEntry`` keyed by ``sid``.

    ``add`` is the only mutator. On duplicate ``sid`` the existing entry is
    upgraded to ``fresh=True`` if the incoming entry is fresh — so a source
    attached in the current turn always renders with a preview even if it
    was also attached in a prior turn.
    """

    entries: list[SourceEntry] = field(default_factory=list)
    _index: dict[str, int] = field(default_factory=dict, repr=False)

    def add(self, entry: SourceEntry) -> None:
        if not entry.sid:
            return
        if not entry.full_text.strip() and not entry.path:
            return
        existing_pos = self._index.get(entry.sid)
        if existing_pos is None:
            self._index[entry.sid] = len(self.entries)
            self.entries.append(entry)
            return
        existing = self.entries[existing_pos]
        # Fresh always wins; otherwise keep the earlier registration.
        if entry.fresh and not existing.fresh:
            self.entries[existing_pos] = entry

    def is_empty(self) -> bool:
        return not self.entries

    def __contains__(self, sid: str) -> bool:
        return sid in self._index


# ---------------------------------------------------------------------------
# Public API: build + render
# ---------------------------------------------------------------------------


async def build_inventory(
    store: SessionStoreProtocol,
    *,
    session_id: str,
    leaf_message_id: int | None,
    current_turn_ordinal: int,
    fresh_attachment_records: Sequence[dict[str, Any]],
    fresh_history_session_ids: Sequence[Any],
    language: str = "en",
    attachment_paths: dict[str, str] | None = None,
    materialize: MaterializeCallback | None = None,
) -> SourceInventory:
    """Compose the session-cumulative inventory for one chat turn.

    Fresh refs are added first (so they shadow historical entries on the
    same sid); historical refs are then collected from the active branch's
    ancestor messages.

    ``attachment_paths`` maps attachment id → workspace copy path for the
    fresh records (the executor materializes them before building). For
    historical attachments — which only become known while walking the
    lineage — ``materialize`` is an optional async callback invoked once
    with the collected records; it keeps this module free of filesystem
    work while still letting the caller put copies on disk.
    """
    paths = attachment_paths or {}
    inv = SourceInventory()
    _add_fresh(
        inv,
        current_turn_ordinal=current_turn_ordinal,
        attachment_records=fresh_attachment_records,
        attachment_paths=paths,
    )
    # History sessions are async (per-id store fetches), keep them in a
    # separate phase so the sync fresh additions don't block.
    await _add_fresh_history(
        inv,
        store=store,
        history_session_ids=fresh_history_session_ids,
        current_turn_ordinal=current_turn_ordinal,
        language=language,
    )
    await _add_historical(
        inv,
        store=store,
        session_id=session_id,
        leaf_message_id=leaf_message_id,
        language=language,
        attachment_paths=paths,
        materialize=materialize,
    )
    return inv


def render_manifest(inv: SourceInventory) -> str:
    """Render the inventory into the manifest text injected into the prompt."""
    if inv.is_empty():
        return ""

    rendered_rows: list[str] = []
    for entry in inv.entries:
        rendered_rows.append(_render_row(entry))

    header = "[Attached Sources]\n"
    if any(entry.path for entry in inv.entries):
        header += (
            "Rows with a `path` field point at the full file on disk — read it "
            "when the preview is not enough. "
        )
    header += (
        "An index of the sources the user has attached in this conversation. "
        "Rows with a `preview` field were attached **this turn**; rows marked "
        "`previously attached (turn N)` were uploaded in earlier turns and show "
        "only their identity. Refer to sources by name; never invent source ids."
    )
    return header + "\n\n" + "\n\n".join(rendered_rows)


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _clip_preview(text: str, limit: int = MANIFEST_PREVIEW_CHARS_FRESH) -> str:
    cleaned = (text or "").strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[:limit].rstrip() + "…"


def _format_size(char_count: int) -> str:
    """Compact size hint for historical rows ('~3 KB', '~120 chars')."""
    if char_count >= 1024:
        return f"~{round(char_count / 1024)} KB"
    return f"~{char_count} chars"


def _render_row(entry: SourceEntry) -> str:
    path_line = f"\n  path: {entry.path}" if entry.path else ""
    if entry.fresh:
        preview = _clip_preview(entry.full_text)
        return (
            f"- id={entry.sid}  type={entry.kind}  name={entry.name!r}"
            f"{path_line}\n  preview: {preview!r}"
        )
    size = f"  size={_format_size(entry.char_count)}  " if entry.char_count else "  "
    return (
        f"- id={entry.sid}  type={entry.kind}  name={entry.name!r}"
        f"{size}source: previously attached (turn {entry.first_seen_turn})"
        f"{path_line}"
    )


# ----- Fresh source addition (current-turn payload) -----------------------


def _add_fresh(
    inv: SourceInventory,
    *,
    current_turn_ordinal: int,
    attachment_records: Sequence[dict[str, Any]],
    attachment_paths: dict[str, str] | None = None,
) -> None:
    """Add the synchronously-available fresh sources (attachments).

    Records whose materialized copy is in ``attachment_paths`` (keyed by
    attachment id) render with a ``path`` row. Image records carry no
    extracted text, so they only surface when a copy exists — the agent
    reads the file instead of a preview.
    """
    for rec in attachment_records:
        filename = str(rec.get("filename") or "file")
        extracted = str(rec.get("extracted_text") or "")
        att_id = str(rec.get("id") or "").strip()
        inv.add(
            SourceEntry(
                # A stable short id for a filename, not a security digest — the
                # whole repo marks these ``usedforsecurity=False`` so bandit's
                # B324 (weak SHA1) does not fire on an identifier.
                sid=(
                    "att-"
                    + hashlib.sha1(filename.encode("utf-8"), usedforsecurity=False).hexdigest()[:12]
                ),
                kind="attachment",
                name=filename,
                full_text=extracted,
                fresh=True,
                first_seen_turn=current_turn_ordinal,
                attachment_id=att_id,
                path=(attachment_paths or {}).get(att_id, "") if att_id else "",
            )
        )


async def _add_fresh_history(
    inv: SourceInventory,
    *,
    store: SessionStoreProtocol,
    history_session_ids: Sequence[Any],
    current_turn_ordinal: int,
    language: str = "en",
) -> None:
    for raw in history_session_ids:
        hs_id = str(raw or "").strip()
        if not hs_id:
            continue
        text, name = await _load_history_session(store, hs_id, language=language)
        if not text:
            continue
        inv.add(
            SourceEntry(
                sid=f"hs-{hs_id}",
                kind="history",
                name=name,
                full_text=text,
                fresh=True,
                first_seen_turn=current_turn_ordinal,
            )
        )


# ----- Historical source collection ---------------------------------------


async def _add_historical(
    inv: SourceInventory,
    *,
    store: SessionStoreProtocol,
    session_id: str,
    leaf_message_id: int | None,
    language: str = "en",
    attachment_paths: dict[str, str],
    materialize: MaterializeCallback | None = None,
) -> None:
    """Walk the active branch's ancestor user messages and pull in
    references they carried. Sources already in ``inv`` (i.e. fresh
    duplicates) are skipped — fresh entries always win.

    Historical attachments are collected first and added afterwards so the
    (optional, async) ``materialize`` callback can run once for the whole
    batch before entries are constructed.
    """
    lineage = await _load_lineage(store, session_id, leaf_message_id)
    pending: list[tuple[int, dict[str, Any]]] = []
    user_turn_ordinal = 0
    for msg in lineage:
        if msg.get("role") != "user":
            continue
        user_turn_ordinal += 1
        await _collect_from_user_message(
            inv,
            store=store,
            msg=msg,
            turn_ordinal=user_turn_ordinal,
            language=language,
            pending_attachments=pending,
        )
    paths = dict(attachment_paths)
    if pending and materialize is not None:
        try:
            paths.update(await materialize([att for _, att in pending]))
        except Exception:
            logger.warning(
                "historical attachment materialization failed; manifest rows will carry no path",
                exc_info=True,
            )
    for turn_ordinal, att in pending:
        _add_historical_attachment(inv, att, turn_ordinal, paths)


def _add_historical_attachment(
    inv: SourceInventory,
    att: dict[str, Any],
    turn_ordinal: int,
    paths: dict[str, str],
) -> None:
    """Add one prior-turn attachment as a historical manifest row."""
    att_id = str(att.get("id", "") or "").strip()
    if not att_id:
        return
    sid = f"at-{att_id}"
    if sid in inv:
        return
    mime = str(att.get("mime_type", "")).lower()
    text = str(att.get("extracted_text") or "")
    path = paths.get(att_id, "")
    if not path:
        # Without a workspace copy the row is only worth rendering when it
        # carries a meaningful preview — the pre-materialization behavior.
        if mime.startswith(_IMAGE_MIME_PREFIX):
            return
        if not text.strip():
            return
    inv.add(
        SourceEntry(
            sid=sid,
            kind="attachment",
            name=str(att.get("filename") or "Untitled file"),
            full_text=text,
            fresh=False,
            first_seen_turn=turn_ordinal,
            attachment_id=att_id,
            path=path,
        )
    )


async def _collect_from_user_message(
    inv: SourceInventory,
    *,
    store: SessionStoreProtocol,
    msg: dict[str, Any],
    turn_ordinal: int,
    language: str = "en",
    pending_attachments: list[tuple[int, dict[str, Any]]],
) -> None:
    """Drain one prior user message into the inventory as historical
    entries. Attachments are pulled from the persisted ``attachments``
    JSON and queued on ``pending_attachments`` (deferred so the caller
    can materialize the whole batch); history refs are pulled from
    ``metadata.request_snapshot`` and re-resolved through their service
    so the historical full text always reflects the current state of the
    referenced object.
    """
    # Attachments — extracted_text was persisted at upload time, no
    # external lookup needed.
    for att in msg.get("attachments") or []:
        if not str(att.get("id", "") or "").strip():
            continue
        pending_attachments.append((turn_ordinal, att))

    snap = (msg.get("metadata") or {}).get("request_snapshot") or {}
    if not isinstance(snap, dict):
        return

    # History sessions — async, one store fetch per id.
    for raw in snap.get("historyReferences") or []:
        hs_id = str(raw or "").strip()
        if not hs_id:
            continue
        sid = f"hs-{hs_id}"
        if sid in inv:
            continue
        text, name = await _load_history_session(store, hs_id, language=language)
        if not text:
            continue
        inv.add(
            SourceEntry(
                sid=sid,
                kind="history",
                name=name,
                full_text=text,
                fresh=False,
                first_seen_turn=turn_ordinal,
            )
        )


# ----- Lineage walker (branch-safe, store-protocol-compatible) ------------


async def _load_lineage(
    store: SessionStoreProtocol,
    session_id: str,
    leaf_message_id: int | None,
) -> list[dict[str, Any]]:
    """Return the active branch's ancestor user/assistant messages in
    chronological order. When ``leaf_message_id`` is ``None`` (legacy
    linear append), returns every message in the session. When it's set
    (branched edit), walks the ``parent_message_id`` chain up from that
    leaf so sibling branches are excluded. Uses ``get_messages`` (which
    every store implements) plus a Python-side parent walk, avoiding a
    sqlite-only ``get_message_path``.
    """
    all_msgs = await store.get_messages(session_id)
    if leaf_message_id is None:
        return all_msgs
    by_id: dict[int, dict[str, Any]] = {}
    for m in all_msgs:
        mid = m.get("id")
        if mid is not None:
            by_id[int(mid)] = m
    chain: list[dict[str, Any]] = []
    current: int | None = int(leaf_message_id)
    safety = 10_000
    while current is not None and safety > 0:
        m = by_id.get(int(current))
        if m is None:
            break
        chain.append(m)
        parent = m.get("parent_message_id")
        current = int(parent) if parent is not None else None
        safety -= 1
    chain.reverse()
    return chain


# ----- Per-type resolvers shared by fresh + historical paths --------------

#: Display labels for transcripts imported from external agent CLIs. The
#: agent-loop integration may grow this set as new import sources appear.
_EXTERNAL_AGENT_LABELS: dict[str, str] = {
    "claude_code": "Claude Code",
    "codex": "Codex",
    "opencode": "OpenCode",
}


def _imported_agent_label(meta: dict[str, Any], lang: str) -> str | None:
    """Return a human label for the external agent a session was imported from,
    or ``None`` when the session is *not* an imported external-agent transcript.

    A referenced session is "imported" when its id carries the ``imported_``
    prefix or its preferences hold the ``import`` block written at import time.
    """
    prefs = meta.get("preferences") if isinstance(meta, dict) else None
    import_meta = prefs.get("import") if isinstance(prefs, dict) else None
    source = str((import_meta or {}).get("source") or "").strip().lower()
    sid = str(meta.get("session_id") or meta.get("id") or "")
    if not source and not sid.startswith("imported_"):
        return None
    if source in _EXTERNAL_AGENT_LABELS:
        return _EXTERNAL_AGENT_LABELS[source]
    return "外部 AI 助手" if lang == "zh" else "an external AI assistant"


def serialize_referenced_transcript(
    meta: dict[str, Any],
    messages: Sequence[dict[str, Any]],
    *,
    language: str = "en",
) -> str:
    """Serialize a *referenced* conversation into a clearly-framed transcript.

    A referenced session is material the user attached for the assistant to
    read and discuss — it is **not** the current conversation. Two failure
    modes motivated this framing:

    * An imported session is a transcript of the user talking to a *different*
      AI agent. Rendered as bare ``## Assistant`` turns it reads exactly like
      the model's own past replies, so the model adopts that agent's first
      person voice and even claims its actions as its own.
    * Even a referenced *native* session is a separate conversation, not the
      current one.

    The fix is structural: prepend an explicit boundary header and name the
    other party (the external agent for imports) so the role label can never
    be confused with the model's own ``assistant`` role. Returns ``""`` when
    there is no content to serialize.
    """
    lang = "zh" if str(language or "en").lower().startswith("zh") else "en"
    agent = _imported_agent_label(meta, lang)
    if agent is not None:
        assistant_label = agent
        header = (
            f"〔以下是用户与外部 AI 助手「{agent}」的历史对话记录，由用户附带进来供你参考和讨论。"
            "这不是你与用户的对话——你没有参与其中，也没有执行其中的任何动作。"
            "请把它当作第三方材料客观对待：复述时用第三人称，不要沿用其口吻，"
            "也不要把其中助手做过的事说成是你做的。〕"
            if lang == "zh"
            else (
                f"[The following is a transcript of a past conversation between the user and an "
                f"external AI assistant ({agent}), attached by the user for your reference and "
                "discussion. This is NOT your conversation with the user — you did not take part "
                "in it and performed none of its actions. Treat it as third-party material: "
                "describe it in the third person, do not adopt its voice, and never claim its "
                "assistant's actions as your own.]"
            )
        )
    else:
        assistant_label = "Assistant"
        header = (
            "〔以下是另一段历史对话记录，由用户附带进来供你参考。它不是当前对话的一部分。〕"
            if lang == "zh"
            else (
                "[The following is a transcript of a separate past conversation, attached by the "
                "user for reference. It is not part of the current conversation.]"
            )
        )
    user_label = "用户" if lang == "zh" else "User"
    lines: list[str] = []
    for message in messages:
        content = str(message.get("content", "") or "").strip()
        if not content:
            continue
        role = str(message.get("role", "")).strip().lower()
        if role == "user":
            label = user_label
        elif role == "assistant":
            label = assistant_label
        else:
            label = role.title() or "Message"
        lines.append(f"## {label}\n{content}")
    if not lines:
        return ""
    return header + "\n\n" + "\n\n".join(lines)


async def _load_history_session(
    store: SessionStoreProtocol,
    history_session_id: str,
    *,
    language: str = "en",
) -> tuple[str, str]:
    """Fetch and serialize a referenced history session into transcript +
    title. Returns ``("", "")`` when the session is empty or missing.
    """
    try:
        meta = await store.get_session(history_session_id)
    except Exception:
        meta = None
    if not meta:
        return "", ""
    try:
        messages_in_hs = await store.get_messages_for_context(history_session_id)
    except Exception:
        messages_in_hs = []
    transcript = serialize_referenced_transcript(meta, messages_in_hs, language=language)
    if not transcript:
        return "", ""
    name = str(meta.get("title", "") or "Untitled session")
    return transcript, name


__all__ = [
    "MANIFEST_PREVIEW_CHARS_FRESH",
    "SourceEntry",
    "SourceInventory",
    "build_inventory",
    "render_manifest",
    "serialize_referenced_transcript",
]
