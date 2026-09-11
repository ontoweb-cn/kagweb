"""Materialize chat attachments into the session workspace.

The manifest tells the agent backend what the user attached; the preview
text alone (2,000 chars for fresh rows) is not the file. This module copies
each stored attachment into ``<session workspace>/attachments/`` — the
directory the CLI/ACP backend runs in — so the manifest can hand the agent
an absolute path it can read with its own file tools.

Ownership stays with the attachment store: these are copies, re-derivable
from the store at any time, keyed by the stable ``attachment_id``.
Materialization is idempotent (an existing same-size copy is reused) and
fail-soft (a file that cannot be copied simply gets no path row; the turn
proceeds). The executor gates the whole step on the configured backend
family running in a filesystem workspace at all.
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
import shutil
from typing import Any

logger = logging.getLogger(__name__)


def _workspace_attachments_dir(session_id: str):
    from kagweb.services.path_service import get_path_service

    return get_path_service().get_task_workspace("chat", session_id) / "attachments"


def _copy_one(store, session_id: str, rec: dict[str, Any]) -> str | None:  # noqa: ANN001
    """Copy one attachment record into the workspace; ``None`` = skip."""
    att_id = str(rec.get("id") or "").strip()
    filename = str(rec.get("filename") or "").strip()
    if not att_id or not filename:
        return None
    src = store.resolve_path(session_id=session_id, attachment_id=att_id, filename=filename)
    if src is None:
        return None
    try:
        dst_dir = _workspace_attachments_dir(session_id)
        # Name-only join: the id prefix is ours, the filename component must
        # never be able to climb out of the attachments directory.
        dst = dst_dir / f"{att_id}_{Path(filename).name}"
        if dst.is_file() and dst.stat().st_size == src.stat().st_size:
            return str(dst)
        dst_dir.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_name(f"{dst.name}.tmp-{os.getpid()}")
        shutil.copyfile(src, tmp)
        os.replace(tmp, dst)
        return str(dst)
    except OSError as exc:
        logger.warning(
            "attachment workspace copy failed for %s (%s): %s",
            att_id,
            filename,
            exc,
        )
        return None


async def materialize_attachments(
    session_id: str,
    records: list[dict[str, Any]],
    *,
    store: Any = None,
) -> dict[str, str]:
    """Copy ``records`` (attachment record dicts) into the session workspace.

    Returns ``{attachment_id: absolute path}`` for every record that was
    copied (or already present). Records without an id/filename, and files
    the store can no longer resolve, are silently left out.
    """
    if not records:
        return {}
    if store is None:
        from kagweb.services.storage.attachment_store import get_attachment_store

        store = get_attachment_store()

    def _all() -> dict[str, str]:
        out: dict[str, str] = {}
        seen: set[str] = set()
        for rec in records:
            att_id = str(rec.get("id") or "").strip()
            if not att_id or att_id in seen:
                continue
            seen.add(att_id)
            path = _copy_one(store, session_id, rec)
            if path:
                out[att_id] = path
        return out

    return await asyncio.to_thread(_all)
