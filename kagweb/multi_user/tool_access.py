"""Per-user exec access resolution (grant v2).

Enforcement point: ``exec_override`` is layered on top of the deployment
exec policy in the chat pipeline's exec gate and in the exec tool itself.
"""

from __future__ import annotations

from .context import get_current_user
from .grants import load_grant


def _current_grant() -> dict | None:
    """The current user's grant, or ``None`` when unrestricted (admin)."""
    user = get_current_user()
    if user.is_admin:
        return None
    return load_grant(user.id)


def exec_override() -> bool | None:
    """Per-user exec override: ``None`` follows the deployment policy."""
    grant = _current_grant()
    if grant is None:
        return None
    value = grant.get("exec_enabled")
    return value if isinstance(value, bool) else None


__all__ = [
    "exec_override",
]
