from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sqlite3
import time

import pytest

from kagweb.services.path_service import PathService
from kagweb.services.session.sqlite_store import SQLiteSessionStore


def test_sqlite_store_defaults_to_data_user_chat_history_db(tmp_path: Path) -> None:
    service = PathService.get_instance()
    original_root = service._project_root
    original_user_dir = service._user_data_dir

    try:
        service._project_root = tmp_path
        service._user_data_dir = tmp_path / "data" / "user"

        store = SQLiteSessionStore()

        assert store.db_path == tmp_path / "data" / "user" / "chat_history.db"
        assert store.db_path.exists()
    finally:
        service._project_root = original_root
        service._user_data_dir = original_user_dir


def test_sqlite_store_migrates_legacy_chat_history_db(tmp_path: Path) -> None:
    service = PathService.get_instance()
    original_root = service._project_root
    original_user_dir = service._user_data_dir

    try:
        service._project_root = tmp_path
        service._user_data_dir = tmp_path / "data" / "user"
        legacy_db = tmp_path / "data" / "chat_history.db"
        legacy_db.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(legacy_db)
        with conn:
            conn.execute("CREATE TABLE legacy (id INTEGER PRIMARY KEY)")
            conn.commit()
        # Windows keeps the file locked until the handle is closed, which
        # would block the store's os.replace migration.
        conn.close()

        store = SQLiteSessionStore()

        assert store.db_path.exists()
        assert not legacy_db.exists()
    finally:
        service._project_root = original_root
        service._user_data_dir = original_user_dir


@pytest.fixture
def store(tmp_path: Path) -> SQLiteSessionStore:
    return SQLiteSessionStore(db_path=tmp_path / "test.db")


def _make_items(*specs):
    """Build notebook entry dicts from (qid, question, is_correct) tuples."""
    items = []
    for qid, question, is_correct in specs:
        items.append(
            {
                "question_id": qid,
                "question": question,
                "question_type": "choice",
                "options": {"A": "opt_a", "B": "opt_b"},
                "user_answer": "A",
                "correct_answer": "B",
                "explanation": "expl",
                "difficulty": "medium",
                "is_correct": is_correct,
            }
        )
    return items


def test_get_session_summaries_batches_counts_and_latest_visible_message(
    store: SQLiteSessionStore,
) -> None:
    first = asyncio.run(store.create_session(title="First", session_id="session-1"))
    second = asyncio.run(store.create_session(title="Second", session_id="session-2"))
    asyncio.run(store.add_message(first["id"], "system", "private setup"))
    asyncio.run(store.add_message(first["id"], "user", "First question"))
    asyncio.run(store.add_message(first["id"], "assistant", "Latest answer"))
    asyncio.run(store.add_message(second["id"], "system", "system only"))

    summaries = asyncio.run(store.get_session_summaries([first["id"], second["id"], first["id"]]))
    by_id = {summary["session_id"]: summary for summary in summaries}

    assert by_id[first["id"]]["message_count"] == 2
    assert by_id[first["id"]]["last_message"] == "Latest answer"
    assert by_id[second["id"]]["message_count"] == 0
    assert by_id[second["id"]]["last_message"] == ""


def test_update_notebook_entry_bookmark_roundtrip(store: SQLiteSessionStore) -> None:
    session = asyncio.run(store.create_session())
    asyncio.run(store.upsert_notebook_entries(session["id"], _make_items(("q1", "Q?", False))))
    eid = asyncio.run(store.list_notebook_entries())["items"][0]["id"]
    assert asyncio.run(store.update_notebook_entry(eid, {"bookmarked": True})) is True
    assert asyncio.run(store.get_notebook_entry(eid))["bookmarked"] is True
    assert asyncio.run(store.update_notebook_entry(eid, {"bookmarked": False})) is True
    assert asyncio.run(store.get_notebook_entry(eid))["bookmarked"] is False
    assert asyncio.run(store.update_notebook_entry(99999, {"bookmarked": True})) is False


def test_update_followup_session_id(store: SQLiteSessionStore) -> None:
    session = asyncio.run(store.create_session())
    asyncio.run(store.upsert_notebook_entries(session["id"], _make_items(("q1", "Q?", False))))
    eid = asyncio.run(store.list_notebook_entries())["items"][0]["id"]
    asyncio.run(store.update_notebook_entry(eid, {"followup_session_id": "sess_fu"}))
    entry = asyncio.run(store.get_notebook_entry(eid))
    assert entry["followup_session_id"] == "sess_fu"


def test_find_notebook_entry(store: SQLiteSessionStore) -> None:
    session = asyncio.run(store.create_session())
    asyncio.run(store.upsert_notebook_entries(session["id"], _make_items(("q1", "Q?", False))))
    found = asyncio.run(store.find_notebook_entry(session["id"], "q1"))
    assert found is not None
    assert found["question_id"] == "q1"
    assert asyncio.run(store.find_notebook_entry(session["id"], "nope")) is None


def test_delete_notebook_entry(store: SQLiteSessionStore) -> None:
    session = asyncio.run(store.create_session())
    asyncio.run(
        store.upsert_notebook_entries(
            session["id"],
            _make_items(
                ("q1", "Q1?", False),
                ("q2", "Q2?", False),
            ),
        )
    )
    eid = asyncio.run(store.list_notebook_entries())["items"][0]["id"]
    assert asyncio.run(store.delete_notebook_entry(eid)) is True
    assert asyncio.run(store.list_notebook_entries())["total"] == 1
    assert asyncio.run(store.delete_notebook_entry(99999)) is False


def test_entries_cascade_on_session_delete(store: SQLiteSessionStore) -> None:
    session = asyncio.run(store.create_session())
    asyncio.run(store.upsert_notebook_entries(session["id"], _make_items(("q1", "Q?", False))))
    assert asyncio.run(store.list_notebook_entries())["total"] == 1
    asyncio.run(store.delete_session(session["id"]))
    assert asyncio.run(store.list_notebook_entries())["total"] == 0


# ── Categories ────────────────────────────────────────────────────


def test_category_crud(store: SQLiteSessionStore) -> None:
    cat = asyncio.run(store.create_category("Math"))
    assert cat["name"] == "Math"
    cats = asyncio.run(store.list_categories())
    assert len(cats) == 1
    assert cats[0]["entry_count"] == 0

    asyncio.run(store.rename_category(cat["id"], "Algebra"))
    cats = asyncio.run(store.list_categories())
    assert cats[0]["name"] == "Algebra"

    asyncio.run(store.delete_category(cat["id"]))
    assert asyncio.run(store.list_categories()) == []


def test_entry_category_association(store: SQLiteSessionStore) -> None:
    session = asyncio.run(store.create_session())
    asyncio.run(store.upsert_notebook_entries(session["id"], _make_items(("q1", "Q?", False))))
    eid = asyncio.run(store.list_notebook_entries())["items"][0]["id"]
    cat = asyncio.run(store.create_category("Physics"))

    assert asyncio.run(store.add_entry_to_category(eid, cat["id"])) is True
    entry = asyncio.run(store.get_notebook_entry(eid))
    assert len(entry["categories"]) == 1
    assert entry["categories"][0]["name"] == "Physics"

    by_cat = asyncio.run(store.list_notebook_entries(category_id=cat["id"]))
    assert by_cat["total"] == 1

    asyncio.run(store.remove_entry_from_category(eid, cat["id"]))
    assert asyncio.run(store.get_entry_categories(eid)) == []


def test_category_cascade_on_entry_delete(store: SQLiteSessionStore) -> None:
    session = asyncio.run(store.create_session())
    asyncio.run(store.upsert_notebook_entries(session["id"], _make_items(("q1", "Q?", False))))
    eid = asyncio.run(store.list_notebook_entries())["items"][0]["id"]
    cat = asyncio.run(store.create_category("History"))
    asyncio.run(store.add_entry_to_category(eid, cat["id"]))
    asyncio.run(store.delete_notebook_entry(eid))
    cats = asyncio.run(store.list_categories())
    assert cats[0]["entry_count"] == 0


# ── Turn deletion / parent-pointer splicing ───────────────────────


def _seed_chat(store: SQLiteSessionStore, turns: int) -> tuple[str, list[int]]:
    """Seed a linear multi-turn chat; returns (session_id, message_ids) where
    message_ids alternate user/assistant per turn."""
    session = asyncio.run(store.create_session())
    sid = session["id"]
    ids: list[int] = []
    parent: int | None = None
    for i in range(turns):
        uid = asyncio.run(store.add_message(sid, "user", f"q{i + 1}", parent_message_id=parent))
        ids.append(uid)
        aid = asyncio.run(store.add_message(sid, "assistant", f"a{i + 1}", parent_message_id=uid))
        ids.append(aid)
        parent = aid
    return sid, ids


def test_delete_first_turn_reparents_descendants(store: SQLiteSessionStore) -> None:
    sid, ids = _seed_chat(store, turns=2)
    u1, _a1, u2, a2 = ids

    result = asyncio.run(store.delete_turn_by_message(sid, u1))
    assert result["deleted"] is True

    remaining = asyncio.run(store.get_messages(sid))
    assert [m["content"] for m in remaining] == ["q2", "a2"]
    assert remaining[0]["parent_message_id"] is None
    assert remaining[1]["parent_message_id"] == u2
    # The surviving chain is fully connected root → leaf.
    path = asyncio.run(store.get_message_path(sid, a2))
    assert [m["content"] for m in path] == ["q2", "a2"]


def test_delete_middle_turn_keeps_chain_connected(store: SQLiteSessionStore) -> None:
    sid, ids = _seed_chat(store, turns=3)
    u1, a1, u2, _a2, u3, a3 = ids

    result = asyncio.run(store.delete_turn_by_message(sid, u2))
    assert result["deleted"] is True

    remaining = asyncio.run(store.get_messages(sid))
    assert [m["content"] for m in remaining] == ["q1", "a1", "q3", "a3"]
    by_content = {m["content"]: m for m in remaining}
    assert by_content["q3"]["parent_message_id"] == a1

    path = asyncio.run(store.get_message_path(sid, a3))
    assert [m["content"] for m in path] == ["q1", "a1", "q3", "a3"]
    # u1 stays the session root.
    assert by_content["q1"]["parent_message_id"] is None
    assert by_content["q1"]["id"] == u1


def test_delete_last_turn_leaves_prefix_intact(store: SQLiteSessionStore) -> None:
    sid, ids = _seed_chat(store, turns=2)
    _u1, a1, u2, _a2 = ids

    result = asyncio.run(store.delete_turn_by_message(sid, u2))
    assert result["deleted"] is True

    remaining = asyncio.run(store.get_messages(sid))
    assert [m["content"] for m in remaining] == ["q1", "a1"]
    assert remaining[0]["parent_message_id"] is None
    assert remaining[1]["parent_message_id"] == remaining[0]["id"]
    assert remaining[1]["id"] == a1


# ── Context messages ──────────────────────────────────────────────


_ASK_USER_EVENTS = [
    {"type": "content", "content": "streamed delta", "metadata": {}},
    {
        "type": "tool_result",
        "metadata": {
            "tool_metadata": {"ask_user": {"questions": [{"id": "level", "prompt": "Your level?"}]}}
        },
    },
    {
        "type": "progress",
        "metadata": {
            "ask_user_resolved": True,
            "answers": [{"questionId": "level", "text": "Beginner"}],
        },
    },
]


def _add_ask_user_turn(store: SQLiteSessionStore, session_id: str) -> None:
    asyncio.run(store.add_message(session_id, "user", "Plan my study"))
    asyncio.run(
        store.add_message(session_id, "assistant", "Here is a plan", events=_ASK_USER_EVENTS)
    )


def test_context_messages_carry_ask_user_events(store: SQLiteSessionStore) -> None:
    session = asyncio.run(store.create_session())
    _add_ask_user_turn(store, session["id"])

    messages = asyncio.run(store.get_messages_for_context(session["id"]))

    assert [m["role"] for m in messages] == ["user", "assistant"]
    # Streamed deltas are dropped; only the ask_user exchange survives, so a
    # later turn can see which questions the learner already answered.
    assert [e["type"] for e in messages[1]["events"]] == ["tool_result", "progress"]


def test_context_messages_carry_private_metadata(store: SQLiteSessionStore) -> None:
    session = asyncio.run(store.create_session())
    state = {"reasoning_content": "private reasoning"}
    asyncio.run(
        store.add_message(
            session["id"],
            "assistant",
            "A direct answer",
            metadata={"provider_response_state": state},
        )
    )

    messages = asyncio.run(store.get_messages_for_context(session["id"]))

    assert messages[0]["metadata"]["provider_response_state"] == state

    public_detail = asyncio.run(store.get_session_with_messages(session["id"]))
    assert public_detail is not None
    assert "provider_response_state" not in public_detail["messages"][0]["metadata"]


def test_branch_context_messages_carry_ask_user_events(store: SQLiteSessionStore) -> None:
    session = asyncio.run(store.create_session())
    _add_ask_user_turn(store, session["id"])
    leaf = asyncio.run(store.add_message(session["id"], "user", "Still not right"))

    messages = asyncio.run(store.get_messages_for_context(session["id"], leaf_message_id=leaf))

    assert [e["type"] for e in messages[1]["events"]] == ["tool_result", "progress"]


def test_branch_context_messages_carry_private_metadata(store: SQLiteSessionStore) -> None:
    session = asyncio.run(store.create_session())
    asyncio.run(store.add_message(session["id"], "user", "Question"))
    state = {"reasoning_content": "branch reasoning"}
    leaf = asyncio.run(
        store.add_message(
            session["id"],
            "assistant",
            "A branched answer",
            metadata={"provider_response_state": state},
        )
    )

    messages = asyncio.run(store.get_messages_for_context(session["id"], leaf_message_id=leaf))

    assert messages[-1]["metadata"]["provider_response_state"] == state
