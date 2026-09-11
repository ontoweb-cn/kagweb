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
