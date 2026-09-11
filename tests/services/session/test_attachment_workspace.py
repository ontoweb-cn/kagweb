"""Materialization of chat attachments into the session workspace.

The manifest's ``path`` rows are only as good as these copies: they must be
faithful (same bytes), idempotent across turns (no rewrite churn), and
fail-soft (an unresolvable source degrades to a path-less row, never a
failed turn).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kagweb.services.session.attachment_workspace import materialize_attachments

pytestmark = pytest.mark.asyncio


class _FakeStore:
    """resolve_path stand-in backed by an in-memory file table."""

    def __init__(self, files: dict[tuple[str, str, str], bytes]) -> None:
        self.files = files

    def resolve_path(self, *, session_id: str, attachment_id: str, filename: str):
        raw = self.files.get((session_id, attachment_id, filename))
        if raw is None:
            return None
        path = Path(self.root) / f"{attachment_id}_{filename}"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        return path


@pytest.fixture
def store(tmp_path: Path) -> _FakeStore:
    fake = _FakeStore({})
    fake.root = tmp_path / "store"
    return fake


@pytest.fixture
def ws_dir(tmp_path: Path, monkeypatch) -> Path:
    target = tmp_path / "ws" / "attachments"
    monkeypatch.setattr(
        "kagweb.services.session.attachment_workspace._workspace_attachments_dir",
        lambda session_id: target,
    )
    return target


async def test_copies_file_and_returns_absolute_path(store, ws_dir) -> None:
    store.files[("s1", "a1", "notes.txt")] = b"hello world"

    paths = await materialize_attachments(
        "s1", [{"id": "a1", "filename": "notes.txt"}], store=store
    )

    assert paths == {"a1": str(ws_dir / "a1_notes.txt")}
    copied = ws_dir / "a1_notes.txt"
    assert copied.is_file()
    assert copied.read_bytes() == b"hello world"
    assert Path(paths["a1"]).is_absolute()


async def test_second_call_reuses_existing_copy(store, ws_dir) -> None:
    store.files[("s1", "a1", "notes.txt")] = b"payload"
    record = {"id": "a1", "filename": "notes.txt"}

    first = await materialize_attachments("s1", [record], store=store)
    stat = (ws_dir / "a1_notes.txt").stat()
    second = await materialize_attachments("s1", [record], store=store)

    assert first == second
    assert (ws_dir / "a1_notes.txt").stat().st_mtime_ns == stat.st_mtime_ns


async def test_changed_source_is_recopied(store, ws_dir) -> None:
    store.files[("s1", "a1", "notes.txt")] = b"v1"
    record = {"id": "a1", "filename": "notes.txt"}
    await materialize_attachments("s1", [record], store=store)

    store.files[("s1", "a1", "notes.txt")] = b"v2 with new length"
    await materialize_attachments("s1", [record], store=store)

    assert (ws_dir / "a1_notes.txt").read_bytes() == b"v2 with new length"


async def test_unresolvable_source_is_omitted(store, ws_dir) -> None:
    paths = await materialize_attachments(
        "s1", [{"id": "ghost", "filename": "gone.txt"}], store=store
    )
    assert paths == {}
    assert not ws_dir.exists() or not any(ws_dir.iterdir())


async def test_filename_stays_flat_inside_the_copy_dir(store, ws_dir) -> None:
    store.files[("s1", "a1", "notes.txt")] = b"x"

    paths = await materialize_attachments(
        "s1", [{"id": "a1", "filename": "../escaped.txt"}], store=store
    )

    # The store refuses to resolve the traversal name, so nothing is copied —
    # and even a hostile id/filename combination cannot escape the copy dir.
    assert paths == {}


async def test_flat_directory_component_is_stripped(store, ws_dir) -> None:
    store.files[("s1", "a1", "sub/dir/notes.txt")] = b"x"
    paths = await materialize_attachments(
        "s1", [{"id": "a1", "filename": "sub/dir/notes.txt"}], store=store
    )
    # The copy name is coerced per component with the store's own rules, so
    # separators in either the id or the filename flatten instead of nesting.
    assert paths == {"a1": str(ws_dir / "a1_notes.txt")}


async def test_incomplete_records_and_duplicate_ids_are_skipped(store, ws_dir) -> None:
    store.files[("s1", "a1", "notes.txt")] = b"x"
    paths = await materialize_attachments(
        "s1",
        [
            {"id": "a1", "filename": "notes.txt"},
            {"id": "a1", "filename": "notes.txt"},
            {"filename": "no-id.txt"},
            {"id": ""},
            {},
        ],
        store=store,
    )
    assert paths == {"a1": str(ws_dir / "a1_notes.txt")}


async def test_empty_records_short_circuit(store, ws_dir) -> None:
    assert await materialize_attachments("s1", [], store=store) == {}
    assert not ws_dir.exists()


# ----- Hostile ids: the payload-controlled id must not steer the copy -----


class _RootTolerantStore:
    """Trust boundary identical to ``LocalDiskAttachmentStore._safe_join``:
    containment is checked against the store *root*, so an id that leaves
    the session dir but stays inside the root still resolves."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def resolve_path(self, *, session_id: str, attachment_id: str, filename: str):
        candidate = (self.root / session_id / f"{attachment_id}_{filename}").resolve()
        try:
            candidate.relative_to(self.root.resolve())
        except ValueError:
            return None
        if not candidate.is_file():
            return None
        return candidate


async def test_hostile_id_cannot_escape_the_copy_dir(tmp_path: Path, ws_dir) -> None:
    """The store tolerates ids like ``../evil`` (root-relative containment),
    so the copy side must coerce the name itself — otherwise the crafted id
    writes outside the attachments directory."""
    store = _RootTolerantStore(tmp_path / "store")
    planted = store.root / "evil_f.txt"
    planted.parent.mkdir(parents=True, exist_ok=True)
    planted.write_bytes(b"payload")

    paths = await materialize_attachments(
        "s1", [{"id": "../evil", "filename": "f.txt"}], store=store
    )

    assert paths == {"../evil": str(ws_dir / "evil_f.txt")}
    assert (ws_dir / "evil_f.txt").read_bytes() == b"payload"
    # The pre-fix bug wrote one level up, next to the attachments dir.
    assert not (ws_dir.parent / "evil_f.txt").exists()


async def test_control_characters_in_id_never_reach_the_copy_name(tmp_path: Path, ws_dir) -> None:
    store = _RootTolerantStore(tmp_path / "store")
    planted = store.root / "s1" / "li\nne_f.txt"
    planted.parent.mkdir(parents=True, exist_ok=True)
    planted.write_bytes(b"x")

    paths = await materialize_attachments(
        "s1", [{"id": "li\nne", "filename": "f.txt"}], store=store
    )

    assert list(paths.values()) == [str(ws_dir / "line_f.txt")]
    assert "\n" not in next(iter(paths.values()))


async def test_concurrent_materialization_publishes_an_intact_copy(store, ws_dir) -> None:
    """Parallel turns of one session materialize the same attachment: the
    per-invocation tmp names must keep every published copy intact and leave
    no debris behind."""
    import asyncio

    payload = bytes(range(256)) * 1024  # 256 KB widens the race window
    store.files[("s1", "a1", "notes.txt")] = payload
    record = {"id": "a1", "filename": "notes.txt"}

    await asyncio.gather(*(materialize_attachments("s1", [record], store=store) for _ in range(8)))

    assert (ws_dir / "a1_notes.txt").read_bytes() == payload
    assert list(ws_dir.glob("*.tmp-*")) == []
