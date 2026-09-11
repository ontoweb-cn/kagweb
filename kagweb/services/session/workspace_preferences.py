"""Canonical workspace ownership stored on chat sessions.

KAGWeb has no workspace modes (the immersive reading / mastery workspaces were
removed with their capabilities). Legacy records may still carry removed keys;
:func:`upgrade_workspace_preferences` strips them so old session rows keep
loading cleanly instead of accumulating dead data forever.
"""

from __future__ import annotations

from typing import Any

# Keys written by removed subsystems. Stripping on load keeps every read path
# clean; the stored row is rewritten without them on the next preference update.
_LEGACY_KEYS = (
    "workspace_mode",  # removed workspaces
    "tools",  # builtin-tool toggles (tools package removed)
    "persona",  # persona subsystem removed
)


def upgrade_workspace_preferences(value: Any) -> dict[str, Any]:
    """Return preferences with removed-subsystem keys stripped."""

    preferences = dict(value) if isinstance(value, dict) else {}
    for key in _LEGACY_KEYS:
        preferences.pop(key, None)
    return preferences


__all__ = ["upgrade_workspace_preferences"]
