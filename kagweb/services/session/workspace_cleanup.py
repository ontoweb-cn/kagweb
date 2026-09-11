"""Reclaim per-session disk artifacts when a session is deleted.

Session deletion used to stop at the database and the attachment store,
leaving workspace residue behind in two places:

* the materialized attachment copies under the session workspace
  (``attachments/``). They are copies — re-derivable from the attachment
  store at any time — so they go with the originals.
* one directory per turn holding the ``events.jsonl`` mirror. Those are
  keyed by *turn id*, a sibling of the session directory rather than a
  child, so they must be attributed through the turns table *before*
  ``delete_session`` cascades the rows away — nothing else remembers the
  mapping afterwards.

Anything else in the session workspace — files the agent itself wrote
while its cwd was that directory — exists nowhere else and deliberately
survives the deletion: the directory is only removed when the copies were
its last content. Every step fails soft (cleanup must never turn a
successful deletion into a failed request), and ids that reach
``rmtree`` are pattern-checked first — the same hostile-id class the
materialization step defends against.
"""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
from typing import Any

from kagweb.services.path_service import get_path_service

logger = logging.getLogger(__name__)

# Server-minted ids (``s_…`` sessions, ``turn_<ms>_<hex>`` turns) are safe to
# build a path from; anything else that ever lands in these columns is not
# worth rmtree-ing over.
_SAFE_DIRNAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")


def _safe_dirname(value: Any) -> str | None:
    name = str(value or "").strip()
    return name if name and _SAFE_DIRNAME.fullmatch(name) else None


def _purge_workspace_sync(session_id: str, turn_roots: list[dict[str, Any]]) -> None:
    path_service = get_path_service()

    for root in turn_roots:
        turn_id = _safe_dirname(root.get("turn_id"))
        if not turn_id:
            continue
        capability = str(root.get("capability") or "").strip() or "chat"
        try:
            task_dir = path_service.get_task_workspace(capability, turn_id)
        except ValueError:
            # Unknown feature label: nothing was ever written under it.
            continue
        shutil.rmtree(task_dir, ignore_errors=True)

    sid = _safe_dirname(session_id)
    if not sid:
        return
    session_dir = path_service.get_task_workspace("chat", sid)
    shutil.rmtree(session_dir / "attachments", ignore_errors=True)
    try:
        session_dir.rmdir()  # succeeds only when the agent wrote nothing else
    except OSError:
        pass


async def purge_session_artifacts(
    session_id: str,
    *,
    turn_roots: list[dict[str, Any]] | None = None,
) -> None:
    """Delete everything a session left on disk, after its DB row is gone.

    ``turn_roots`` is the snapshot taken from
    ``store.list_turn_workspace_roots(session_id)`` *before* the delete;
    stores without that method pass nothing and only the session workspace
    is reclaimed. Fail-soft throughout, current path scope only (a
    cross-scope admin delete leaves the other scope's files alone).
    """
    try:
        from kagweb.services.storage.attachment_store import get_attachment_store

        await get_attachment_store().delete_session(session_id)
    except Exception:
        logger.exception("failed to clean up attachments for session %s", session_id)
    try:
        await asyncio.to_thread(_purge_workspace_sync, session_id, list(turn_roots or []))
    except Exception:
        logger.exception("failed to clean up workspace for session %s", session_id)
