"""The per-turn Attached Sources manifest.

`SourceEntry` is a frozen dataclass whose shape has changed across the fork;
the attachment path was still constructing it with a field that no longer
exists, so any turn carrying an attachment raised TypeError before the turn
could start. These cases pin the constructor contract and the inventory's
dedup rules.
"""

from __future__ import annotations

import pytest

from kagweb.services.session.source_inventory import (
    SourceEntry,
    SourceInventory,
    _add_fresh,
    build_inventory,
    render_manifest,
)

pytestmark = pytest.mark.asyncio


def entry(sid: str, *, fresh: bool = True, text: str = "body") -> SourceEntry:
    return SourceEntry(
        sid=sid,
        kind="attachment",
        name=f"{sid}.txt",
        full_text=text,
        fresh=fresh,
        first_seen_turn=1,
    )


def test_a_fresh_attachment_becomes_a_rendered_manifest_row() -> None:
    inv = SourceInventory()

    _add_fresh(
        inv,
        current_turn_ordinal=3,
        attachment_records=[{"filename": "notes.txt", "extracted_text": "hello world"}],
    )

    assert len(inv.entries) == 1
    row = inv.entries[0]
    assert row.kind == "attachment"
    assert row.name == "notes.txt"
    assert row.full_text == "hello world"
    assert row.fresh is True
    assert row.first_seen_turn == 3

    manifest = render_manifest(inv)
    assert "notes.txt" in manifest
    assert "hello world" in manifest


def test_add_keeps_the_index_and_lets_fresh_win() -> None:
    inv = SourceInventory()
    inv.add(entry("a", fresh=False, text="old"))

    assert "a" in inv
    assert inv.entries[0].full_text == "old"

    # A fresh re-attachment replaces the historical row in place.
    inv.add(entry("a", fresh=True, text="new"))
    assert len(inv.entries) == 1
    assert inv.entries[0].full_text == "new"

    # An empty source is not a source.
    inv.add(entry("b", text="   "))
    assert "b" not in inv
    assert len(inv.entries) == 1


class _EmptyStore:
    """Minimal store: the lineage walk asks for the session's messages."""

    async def get_messages(self, session_id: str) -> list[dict]:
        return []


async def test_build_inventory_accepts_a_fresh_attachment_end_to_end() -> None:
    """The real entry point a chat turn calls: it used to raise TypeError
    before the turn could start whenever the turn carried an attachment."""
    inv = await build_inventory(
        _EmptyStore(),
        session_id="s1",
        leaf_message_id=None,
        current_turn_ordinal=2,
        fresh_attachment_records=[
            {"filename": "spec.md", "extracted_text": "# Spec\nbody"},
        ],
        fresh_history_session_ids=[],
    )

    manifest = render_manifest(inv)
    assert "spec.md" in manifest
    assert "# Spec" in manifest


# ----- Path rows: materialized workspace copies ---------------------------


async def test_fresh_path_row_renders_and_turns_on_the_header_sentence() -> None:
    inv = await build_inventory(
        _EmptyStore(),
        session_id="s1",
        leaf_message_id=None,
        current_turn_ordinal=1,
        fresh_attachment_records=[
            {
                "id": "a1",
                "filename": "notes.txt",
                "extracted_text": "first slide",
            }
        ],
        fresh_history_session_ids=[],
        attachment_paths={"a1": "/ws/attachments/a1_notes.txt"},
    )
    manifest = render_manifest(inv)

    assert "path: /ws/attachments/a1_notes.txt" in manifest
    assert "read it when the preview is not enough" in manifest


async def test_without_paths_there_is_no_path_nor_header_sentence() -> None:
    inv = await build_inventory(
        _EmptyStore(),
        session_id="s1",
        leaf_message_id=None,
        current_turn_ordinal=1,
        fresh_attachment_records=[{"id": "a1", "filename": "notes.txt", "extracted_text": "body"}],
        fresh_history_session_ids=[],
    )
    manifest = render_manifest(inv)

    assert "path:" not in manifest
    assert "read it when the preview is not enough" not in manifest


async def test_image_record_surfaces_only_through_its_copy() -> None:
    inv = SourceInventory()
    _add_fresh(
        inv,
        current_turn_ordinal=1,
        attachment_records=[
            {"id": "img1", "filename": "chart.png", "mime_type": "image/png"},
            {"id": "img2", "filename": "kept.png", "mime_type": "image/png"},
        ],
        attachment_paths={"img2": "/ws/attachments/img2_kept.png"},
    )
    manifest = render_manifest(inv)

    # No extracted text and no copy → invisible (pre-materialization behavior).
    assert "chart.png" not in manifest
    # With a copy: a path row even though there is no preview to show.
    assert "kept.png" in manifest
    assert "path: /ws/attachments/img2_kept.png" in manifest
    assert "preview:" not in manifest.split("kept.png", 1)[1].split("\n", 2)[1]


class _RecordingStore:
    """Minimal store: returns one prior user message carrying attachments."""

    def __init__(self, messages: list[dict]) -> None:
        self._messages = messages

    async def get_messages(self, session_id: str) -> list[dict]:
        return self._messages


async def test_historical_attachments_materialize_through_the_callback() -> None:
    prior = {
        "role": "user",
        "content": "earlier turn",
        "attachments": [
            {
                "id": "h1",
                "filename": "old.txt",
                "mime_type": "text/plain",
                "extracted_text": "old body",
            }
        ],
    }
    seen: list[list[dict]] = []

    async def _materialize(records):
        seen.append(list(records))
        return {"h1": "/ws/attachments/h1_old.txt"}

    inv = await build_inventory(
        _RecordingStore([prior]),
        session_id="s1",
        leaf_message_id=None,
        current_turn_ordinal=2,
        fresh_attachment_records=[],
        fresh_history_session_ids=[],
        materialize=_materialize,
    )
    manifest = render_manifest(inv)

    assert [rec["id"] for rec in seen[0]] == ["h1"]
    assert "path: /ws/attachments/h1_old.txt" in manifest
    assert "previously attached (turn 1)" in manifest


async def test_historical_without_materialization_keeps_the_text_only_filter() -> None:
    prior = {
        "role": "user",
        "content": "earlier turn",
        "attachments": [
            {"id": "h1", "filename": "pic.png", "mime_type": "image/png"},
            {"id": "h2", "filename": "blob.bin", "mime_type": "application/octet-stream"},
            {
                "id": "h3",
                "filename": "doc.txt",
                "mime_type": "text/plain",
                "extracted_text": "real text",
            },
        ],
    }

    inv = await build_inventory(
        _RecordingStore([prior]),
        session_id="s1",
        leaf_message_id=None,
        current_turn_ordinal=2,
        fresh_attachment_records=[],
        fresh_history_session_ids=[],
    )
    manifest = render_manifest(inv)

    assert "pic.png" not in manifest
    assert "blob.bin" not in manifest
    assert "doc.txt" in manifest


async def test_materialize_failure_degrades_to_pathless_rows() -> None:
    prior = {
        "role": "user",
        "content": "earlier turn",
        "attachments": [
            {
                "id": "h1",
                "filename": "old.txt",
                "mime_type": "text/plain",
                "extracted_text": "old body",
            }
        ],
    }

    async def _boom(records):
        raise OSError("disk gone")

    inv = await build_inventory(
        _RecordingStore([prior]),
        session_id="s1",
        leaf_message_id=None,
        current_turn_ordinal=2,
        fresh_attachment_records=[],
        fresh_history_session_ids=[],
        materialize=_boom,
    )
    manifest = render_manifest(inv)

    assert "old.txt" in manifest
    assert "path:" not in manifest
