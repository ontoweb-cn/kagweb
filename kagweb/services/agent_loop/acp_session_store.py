"""Durable record of the agent-side ACP session behind each KAGWeb session.

An ACP child keeps its own conversation state — history, compression chain —
inside the agent process, addressed by an opaque session id that the agent
mints on ``new_session``. That id used to live only on the live
:class:`~kagweb.services.agent_loop.acp_backend.AcpSessionHandle`, so it
survived a child crash but not a KAGWeb restart: the next turn called
``new_session`` and the agent answered with no memory of the conversation.

Recording the id per KAGWeb session lets a respawned child re-attach with
``load_session`` instead. One file per session, keyed by a hash of the
session key: a read-modify-write of one shared file would race between
concurrent sessions, and hashing keeps an id from any source out of a path
(the same hostile-id class ``workspace_cleanup`` defends against). The key
itself is stored in the file so a stale entry can be recognized.

Every operation fails soft: losing this record only costs the agent's memory
of the conversation, which must never fail a turn.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

from kagweb.services.path_service import get_path_service

logger = logging.getLogger(__name__)

_STORE_DIRNAME = "acp_sessions"
_VERSION = 1


def _store_dir() -> Path:
    return get_path_service().get_user_root() / "runtime" / _STORE_DIRNAME


def _entry_path(session_key: str) -> Path:
    digest = hashlib.sha256(session_key.encode("utf-8")).hexdigest()[:32]
    return _store_dir() / f"{digest}.json"


def load_acp_session(session_key: str, *, config_key: str = "") -> tuple[str, str] | None:
    """The stored ``(session_id, cwd)`` for *session_key*, or ``None``.

    An entry written for a different backend configuration is ignored: the
    id addresses a session inside one agent install, so reusing it after the
    operator repointed the profile would attach to the wrong agent's session.
    """
    if not session_key:
        return None
    path = _entry_path(session_key)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except Exception:  # noqa: BLE001 - a damaged file is the same as no file
        logger.debug("Unreadable ACP session record %s", path, exc_info=True)
        return None
    if not isinstance(payload, dict):
        return None
    # A record this build cannot read is treated as absent rather than
    # guessed at: the next turn simply starts a fresh agent session. Bumping
    # ``_VERSION`` is therefore how a future shape change invalidates old
    # entries — without this check the field would be write-only decoration.
    if payload.get("version") != _VERSION:
        return None
    if str(payload.get("session_key") or "") != session_key:
        return None
    stored_config = str(payload.get("config_key") or "")
    if config_key and stored_config and stored_config != config_key:
        return None
    session_id = str(payload.get("acp_session_id") or "").strip()
    if not session_id:
        return None
    return session_id, str(payload.get("cwd") or "")


def save_acp_session(
    session_key: str,
    *,
    config_key: str = "",
    session_id: str,
    cwd: str = "",
) -> None:
    """Record the agent session id attached to *session_key*."""
    if not session_key or not session_id:
        return
    from kagweb.services.file_io import atomic_write_json

    payload: dict[str, Any] = {
        "version": _VERSION,
        "session_key": session_key,
        "config_key": config_key,
        "acp_session_id": session_id,
        "cwd": cwd,
    }
    try:
        atomic_write_json(_entry_path(session_key), payload)
    except Exception:  # noqa: BLE001 - never fail a turn over bookkeeping
        logger.debug("Could not persist ACP session record", exc_info=True)


def forget_acp_session(session_key: str) -> None:
    """Drop the record for *session_key* (best effort)."""
    if not session_key:
        return
    try:
        _entry_path(session_key).unlink(missing_ok=True)
    except Exception:  # noqa: BLE001 - deletion is best effort
        logger.debug("Could not remove ACP session record", exc_info=True)


def forget_acp_sessions_for(session_id: str) -> int:
    """Drop every record belonging to a KAGWeb session, consult children included.

    Consults use derived keys (``"<session>::consult::<profile>"``), so the
    caller cannot name them; the stored key is matched instead of the hashed
    filename. Returns how many records were removed.
    """
    if not session_id:
        return 0
    root = _store_dir()
    try:
        entries = list(root.glob("*.json"))
    except Exception:  # noqa: BLE001 - a missing directory means nothing to do
        return 0
    removed = 0
    for path in entries:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 - skip unreadable files
            continue
        key = str(payload.get("session_key") or "") if isinstance(payload, dict) else ""
        if key == session_id or key.startswith(f"{session_id}::"):
            try:
                path.unlink(missing_ok=True)
                removed += 1
            except Exception:  # noqa: BLE001 - best effort
                logger.debug("Could not remove ACP session record %s", path, exc_info=True)
    return removed


__all__ = [
    "forget_acp_session",
    "forget_acp_sessions_for",
    "load_acp_session",
    "save_acp_session",
]
