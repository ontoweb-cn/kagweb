"""Durable record of the agent-side CLI session behind each KAGWeb session.

The one-shot CLI presets spawn a fresh child per turn, but the agents
themselves persist conversations (claude: `~/.claude/projects/*.jsonl`
addressed by `--session-id`; codex: `~/.codex/sessions` addressed by the
`thread.started` thread id). Recording the agent session id per KAGWeb
session lets later turns re-attach with the agent's own resume flag
instead of re-inlining a budget-truncated transcript — the agent keeps
its own lossless history and spends its own context on it.

Same shape and the same fail-soft contract as
:mod:`~kagweb.services.agent_loop.acp_session_store` (one hashed file per
session key, stored key cross-checked, version-gated): losing the record
only costs continuity, which must never fail a turn. Deliberately a
sibling module rather than a shared table — the ACP record's
``config_key`` semantics (agent-install binding) do not transfer, and a
shared file would couple two unrelated validity rules.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

from kagweb.services.path_service import get_path_service

logger = logging.getLogger(__name__)

_STORE_DIRNAME = "agent_sessions"
_VERSION = 1


def _store_dir() -> Path:
    return get_path_service().get_user_root() / "runtime" / _STORE_DIRNAME


def _entry_path(session_key: str) -> Path:
    digest = hashlib.sha256(session_key.encode("utf-8")).hexdigest()[:32]
    return _store_dir() / f"{digest}.json"


def load_agent_session(session_key: str) -> str | None:
    """The stored agent session id for *session_key*, or ``None``."""
    if not session_key:
        return None
    path = _entry_path(session_key)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except Exception:  # noqa: BLE001 - a damaged file is the same as no file
        logger.debug("Unreadable agent session record %s", path, exc_info=True)
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("version") != _VERSION:
        return None
    if str(payload.get("session_key") or "") != session_key:
        return None
    session_id = str(payload.get("agent_session_id") or "").strip()
    return session_id or None


def save_agent_session(session_key: str, *, agent_session_id: str, cwd: str = "") -> None:
    """Record the agent session id attached to *session_key*."""
    if not session_key or not agent_session_id:
        return
    from kagweb.services.file_io import atomic_write_json

    payload: dict[str, Any] = {
        "version": _VERSION,
        "session_key": session_key,
        "agent_session_id": agent_session_id,
        "cwd": cwd,
    }
    try:
        atomic_write_json(_entry_path(session_key), payload)
    except Exception:  # noqa: BLE001 - never fail a turn over bookkeeping
        logger.debug("Could not persist agent session record", exc_info=True)


def forget_agent_session(session_key: str) -> None:
    """Drop the record for *session_key* (best effort)."""
    if not session_key:
        return
    try:
        _entry_path(session_key).unlink(missing_ok=True)
    except Exception:  # noqa: BLE001 - deletion is best effort
        logger.debug("Could not remove agent session record %s", session_key, exc_info=True)


def forget_agent_sessions_for(session_id: str) -> int:
    """Drop every record belonging to a KAGWeb session.

    Keys are ``"<preset>:<session_id>"``, so the caller cannot name them;
    the stored key is matched instead of the hashed filename. Returns how
    many records were removed.
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
        if key == session_id or key.endswith(f":{session_id}"):
            try:
                path.unlink(missing_ok=True)
                removed += 1
            except Exception:  # noqa: BLE001 - best effort
                logger.debug("Could not remove agent session record %s", path, exc_info=True)
    return removed


__all__ = [
    "forget_agent_session",
    "forget_agent_sessions_for",
    "load_agent_session",
    "save_agent_session",
]
