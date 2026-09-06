"""Canonical workspace ownership stored on chat sessions.

KAGWeb has no workspace modes (the immersive reading / mastery workspaces were
removed with their capabilities). Legacy records may still carry a
``workspace_mode`` value; :func:`upgrade_workspace_preferences` strips it so
old session rows keep loading cleanly.
"""

from __future__ import annotations

from typing import Any


def upgrade_workspace_preferences(value: Any) -> dict[str, Any]:
    """Return preferences with any legacy workspace mode removed."""

    preferences = dict(value) if isinstance(value, dict) else {}
    preferences.pop("workspace_mode", None)
    return preferences


__all__ = ["upgrade_workspace_preferences"]
