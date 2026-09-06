"""Partner workspace layout + asset provisioning.

The partner workspace is a verbatim clone of the chat user-workspace format
(``PathService`` layout), so the chat agent loop's tools read it natively:

    data/partners/<id>/workspace/          ← synthetic scope root
    └── user/
        ├── workspace/
        │   ├── SOUL.md                    ← the partner's persona
        │   ├── skills/<name>/SKILL.md     ← copied skills (read_skill)
        │   ├── notebook/…                 ← copied notebooks (list_notebook/write_note)
        │   └── memory/…
        └── settings/ …

Provisioning runs in the *requesting user's* context: sources are resolved
with that user's permissions (``resolve_kb`` / assigned-skill grants), then
copied into the partner scope as plain files. All three asset classes are
self-contained on disk, so a copy is a complete transfer:

* KB: the whole ``<kb>/`` tree (raw + LlamaIndex ``version-N`` dirs); the
  partner-side ``KnowledgeBaseManager`` auto-registers it on first list.
* Skill: the whole ``<name>/`` dir (SKILL.md + references).
* Notebook: ``<id>.json`` plus its ``notebooks_index.json`` entry.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from deepmentor.multi_user.paths import (
    ensure_scope_workspace,
    get_admin_path_service,
    get_path_service_for_scope,
)
from deepmentor.services.partners.scope import partner_scope
from deepmentor.services.path_service import PathService

logger = logging.getLogger(__name__)


def _requester_path_service() -> PathService:
    """Path service for the user driving this provisioning call.

    Resolved through ``get_current_user()`` (which falls back to the local
    admin) rather than ``get_path_service()`` — the latter short-circuits to
    the process-default instance when no user contextvar is set, bypassing
    scope resolution entirely.
    """
    from deepmentor.multi_user.context import get_current_user

    return get_path_service_for_scope(get_current_user().scope)


SOUL_FILENAME = "SOUL.md"

DEFAULT_SOUL = """# Soul

I am a learning companion. I help with questions patiently and clearly,
adapt to the user's level, and value accuracy over speed.
"""


def ensure_partner_workspace(partner_id: str) -> Path:
    """Create the full chat-format workspace tree; returns the scope root."""
    return ensure_scope_workspace(partner_scope(partner_id))


def strip_frontmatter(text: str) -> str:
    """Drop a leading YAML frontmatter block (``---`` … ``---``) if present.

    Used when cloning a chat persona (PERSONA.md carries name/description
    frontmatter) into a partner SOUL.md, which is plain markdown.
    """
    raw = (text or "").lstrip()
    if not raw.startswith("---"):
        return text or ""
    end = raw.find("\n---", 3)
    if end == -1:
        return text or ""
    return raw[end + 4 :].lstrip("\n")


def _partner_path_service(partner_id: str) -> PathService:
    return PathService(workspace_root=ensure_partner_workspace(partner_id))


# ── Soul ──────────────────────────────────────────────────────────


def soul_path(partner_id: str) -> Path:
    return _partner_path_service(partner_id).get_workspace_dir() / SOUL_FILENAME


def read_soul(partner_id: str) -> str:
    path = soul_path(partner_id)
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        logger.exception("Failed to read SOUL.md for partner %s", partner_id)
        return ""


def write_soul(partner_id: str, content: str) -> None:
    path = soul_path(partner_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content or "", encoding="utf-8")


# ── Asset provisioning ─────────────────────────────────────────────


def list_assets(partner_id: str) -> dict[str, list[dict[str, Any]]]:
    """Assets copied into the partner workspace.

    Knowledge bases, skills and notebooks are no longer provisioned, so the
    report is always empty; kept for API compatibility with existing clients.
    """
    ensure_partner_workspace(partner_id)
    return {"knowledge_bases": [], "skills": [], "notebooks": []}


def remove_asset(partner_id: str, asset_type: str, name: str) -> bool:
    """Remove a provisioned asset. No asset types remain, so this is a no-op
    that reports "nothing removed" for any legacy name."""
    ensure_partner_workspace(partner_id)
    if asset_type not in {"knowledge_base", "skill", "notebook"}:
        raise ValueError(f"Unknown asset type: {asset_type}")
    if "/" in name or "\\" in name or name.startswith("."):
        raise ValueError("Invalid asset name")
    return False


def provision_assets(
    partner_id: str,
    *,
    knowledge_bases: list[str] | None = None,
    skills: list[str] | None = None,
    notebooks: list[str] | None = None,
) -> dict[str, Any]:
    """Copy the requested assets into the partner workspace.

    Knowledge bases, skills and notebooks were removed with the capability
    layer, so there is nothing left to provision; the parameters are kept for
    API compatibility and ignored. Returns an empty report.
    """
    ensure_partner_workspace(partner_id)
    return {
        "copied": {"knowledge_bases": [], "skills": [], "notebooks": []},
        "errors": [],
    }
