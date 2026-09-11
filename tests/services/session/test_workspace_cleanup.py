"""Session deletion reclaims the workspace: materialized copies, the
per-turn ``events.jsonl`` mirrors, and the session directory itself — but
only when the agent wrote nothing else there."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from kagweb.services.path_service import PathService
from kagweb.services.session.sqlite_store import SQLiteSessionStore
from kagweb.services.session.workspace_cleanup import (
    _purge_workspace_sync,
    purge_session_artifacts,
)


@pytest.fixture
def store(tmp_path: Path) -> SQLiteSessionStore:
    return SQLiteSessionStore(db_path=tmp_path / "test.db")


@pytest.fixture
def chat_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A PathService rooted at ``tmp_path`` behind the cleanup module's
    scope accessor, plus a recording attachment-store stand-in."""
    path_service = PathService(workspace_root=tmp_path)
    monkeypatch.setattr(
        "kagweb.services.session.workspace_cleanup.get_path_service",
        lambda: path_service,
    )
    store_deletions: list[str] = []

    class _StoreStub:
        async def delete_session(self, session_id: str) -> None:
            store_deletions.append(session_id)

    monkeypatch.setattr(
        "kagweb.services.storage.attachment_store.get_attachment_store",
        lambda: _StoreStub(),
    )
    # Feature root for the chat capability: <tmp>/user/workspace/chat/chat.
    return path_service.get_workspace_feature_dir("chat") / "chat"


def test_list_turn_workspace_roots_covers_every_status(store: SQLiteSessionStore) -> None:
    session = asyncio.run(store.create_session())
    running = asyncio.run(store.begin_turn(session["id"], capability="chat"))
    asyncio.run(store.transition_turn(running["id"], "completed"))
    # A session holds one active turn at a time; the second exists only in
    # a terminal state.
    failed = asyncio.run(store.begin_turn(session["id"], capability="chat"))
    asyncio.run(store.transition_turn(failed["id"], "failed"))

    roots = asyncio.run(store.list_turn_workspace_roots(session["id"]))

    assert sorted(r["turn_id"] for r in roots) == sorted([running["id"], failed["id"]])
    assert all(r["capability"] == "chat" for r in roots)
    # The snapshot is the only thing that survives the delete.
    asyncio.run(store.delete_session(session["id"]))
    assert asyncio.run(store.list_turn_workspace_roots(session["id"])) == []


def test_purge_removes_copies_turn_dirs_and_the_empty_session_dir(
    store: SQLiteSessionStore, chat_root: Path
) -> None:
    session = asyncio.run(store.create_session())
    sid = session["id"]
    turn = asyncio.run(store.begin_turn(sid, capability="chat"))
    copies_dir = chat_root / sid / "attachments"
    copies_dir.mkdir(parents=True)
    (copies_dir / "a1_notes.txt").write_text("copy", encoding="utf-8")
    events = chat_root / turn["id"]
    events.mkdir(parents=True)
    (events / "events.jsonl").write_text("{}\n", encoding="utf-8")

    _purge_workspace_sync(sid, asyncio.run(store.list_turn_workspace_roots(sid)))

    assert not copies_dir.exists()
    assert not events.exists()
    assert not (chat_root / sid).exists()


def test_agent_artifacts_outlive_the_deletion(chat_root: Path) -> None:
    session_dir = chat_root / "s_artifacts"
    (session_dir / "attachments").mkdir(parents=True)
    (session_dir / "attachments" / "a1_notes.txt").write_text("copy", encoding="utf-8")
    (session_dir / "report.md").write_text("agent output", encoding="utf-8")

    _purge_workspace_sync("s_artifacts", [])

    assert (session_dir / "report.md").exists()
    assert not (session_dir / "attachments").exists()


def test_purge_skips_hostile_ids(chat_root: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    marker = outside / "keep.txt"
    marker.write_text("still here", encoding="utf-8")

    _purge_workspace_sync(
        f"../{outside.name}",
        [{"turn_id": "../evil", "capability": "chat"}, {"turn_id": "  ", "capability": "chat"}],
    )

    assert marker.exists()
    assert not (chat_root / ".." / outside.name).exists()
    assert not (chat_root / "evil").exists()


def test_unknown_capability_root_is_skipped(chat_root: Path) -> None:
    _purge_workspace_sync("s_ok", [{"turn_id": "turn_1_x", "capability": "no_such_feature"}])

    assert not (chat_root.parent.parent / "no_such_feature").exists()


def test_purge_session_artifacts_is_fail_soft(
    store: SQLiteSessionStore, chat_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _BrokenStore:
        async def delete_session(self, session_id: str) -> None:
            raise RuntimeError("store exploded")

    monkeypatch.setattr(
        "kagweb.services.storage.attachment_store.get_attachment_store",
        lambda: _BrokenStore(),
    )
    session = asyncio.run(store.create_session())
    sid = session["id"]
    (chat_root / sid).mkdir(parents=True)

    asyncio.run(purge_session_artifacts(sid, turn_roots=[]))

    # The workspace half still ran despite the store blowing up.
    assert not (chat_root / sid).exists()


def test_purge_session_artifacts_end_to_end(store: SQLiteSessionStore, chat_root: Path) -> None:
    session = asyncio.run(store.create_session())
    sid = session["id"]
    turn = asyncio.run(store.begin_turn(sid, capability="chat"))
    copies = chat_root / sid / "attachments"
    copies.mkdir(parents=True)
    (copies / "a1_notes.txt").write_text("copy", encoding="utf-8")
    events = chat_root / turn["id"]
    events.mkdir(parents=True)
    (events / "events.jsonl").write_text("{}\n", encoding="utf-8")

    turn_roots = asyncio.run(store.list_turn_workspace_roots(sid))
    asyncio.run(purge_session_artifacts(sid, turn_roots=turn_roots))

    assert not copies.exists()
    assert not events.exists()
    assert not (chat_root / sid).exists()
