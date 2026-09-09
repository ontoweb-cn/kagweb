"""Read the ``agent_loop`` block of the runtime system settings.

The block is persisted in ``data/user/settings/system.json`` and normalized
by :class:`kagweb.services.config.runtime_settings.RuntimeSettingsService`
(the defaults live there, next to the other blocks). This module gives the
agent-loop layer typed accessors so capabilities never touch the raw
settings dict — most importantly the **primary / consult split**: the
primary profile drives the turn, the other enabled profiles can be
consulted by it (see ``consult.py`` and ARCHITECTURE.md).
"""

from __future__ import annotations

from typing import Any

#: Settings shape (defaults defined in runtime_settings.DEFAULT_SYSTEM_SETTINGS):
#:
#:   "agent_loop": {
#:     "version": 2,
#:     "profiles": [                  # named agent loops, any mix of families
#:       {
#:         "id": "default",           # stable id (primary points here)
#:         "name": "本地 Intellect",   # display label
#:         "preset": "intellect",     # backend kind (see builtin.PRESETS)
#:         "enabled": True,
#:         # CLI family fields:
#:         "command": "",             # override the preset's executable
#:         "args": [],                # extra argv ("{prompt}" placeholder)
#:         "env": {},                 # the ONLY credentials the child gets
#:         "session_workspace": True, # per-session working directory
#:         # HTTP family fields:
#:         "url": "",                 # service base URL (required)
#:         "turn_path": "/agent/turn",
#:         "headers": {},             # extra headers (auth schemes etc.)
#:         "api_key": "",             # Authorization: Bearer …
#:         # shared:
#:         "timeout_seconds": 900,    # per-turn wall clock cap
#:         "consult_enabled": True,   # may the primary consult this profile
#:         "approval_timeout_seconds": 60,  # approval park budget (5..600)
#:         "approval_default": "deny",      # decision when no user answers
#:       }, …
#:     ],
#:     "primary": "default",          # "" = framework-shell stub
#:     "consult_budget": 3,           # max consults per turn (0..12)
#:   }
#:


def get_agent_loop_settings() -> dict[str, Any]:
    """Return the normalized ``agent_loop`` settings block."""
    from kagweb.services.config.runtime_settings import RuntimeSettingsService

    system = RuntimeSettingsService.get_instance().load_system()
    block = system.get("agent_loop")
    return dict(block) if isinstance(block, dict) else {}


def agent_loop_backend_name() -> str:
    """Return the primary profile's preset name ("" when unset)."""
    primary = resolve_primary_profile()
    return str(primary.get("preset") or "") if primary else ""


def _as_profile_list(block: dict[str, Any]) -> list[dict[str, Any]]:
    profiles = block.get("profiles")
    if isinstance(profiles, list):
        return [dict(item) for item in profiles if isinstance(item, dict)]
    return []


def _legacy_flat_profile(block: dict[str, Any]) -> dict[str, Any] | None:
    """Tolerate v1 flat blocks (tests, older callers): wrap as one profile."""
    preset = str(block.get("backend") or "").strip()
    if not preset:
        return None
    return {"id": "default", "name": preset, "preset": preset, "enabled": True, **block}


def resolve_primary_profile(block: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """The profile that drives the turn, or ``None`` for the shell stub.

    v2 blocks resolve by the ``primary`` id (a disabled primary means the
    stub — the operator paused the conversation backend on purpose); v1
    flat blocks wrap into a single profile so legacy callers keep working.
    """
    settings = block if block is not None else get_agent_loop_settings()
    if not isinstance(settings, dict) or not settings:
        return None
    if "profiles" not in settings:
        return _legacy_flat_profile(settings)
    primary_id = str(settings.get("primary") or "").strip()
    if not primary_id:
        return None
    for profile in _as_profile_list(settings):
        if str(profile.get("id")) == primary_id:
            return profile if profile.get("enabled") else None
    return None


def consult_profiles(block: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Enabled, consultable profiles other than the primary — in file order."""
    settings = block if block is not None else get_agent_loop_settings()
    if not isinstance(settings, dict) or not settings:
        return []
    primary = resolve_primary_profile(settings)
    primary_id = str(primary.get("id")) if primary else ""
    return [
        profile
        for profile in _as_profile_list(settings)
        if profile.get("enabled")
        and profile.get("consult_enabled")
        and str(profile.get("id")) != primary_id
        and str(profile.get("preset") or "").strip()
    ]


def find_consult_profile(block: dict[str, Any], reference: str) -> dict[str, Any] | None:
    """Resolve a consult directive's ``agent`` reference to a profile.

    Matches by id, then display name, then preset name (all
    case-insensitive) so a backend writing ``"hermes"`` or the configured
    label both work.
    """
    wanted = str(reference or "").strip().lower()
    if not wanted:
        return None
    candidates = consult_profiles(block)
    for key in ("id", "name", "preset"):
        for profile in candidates:
            if str(profile.get(key) or "").strip().lower() == wanted:
                return profile
    return None


def consult_budget(block: dict[str, Any] | None = None) -> int:
    settings = block if block is not None else get_agent_loop_settings()
    try:
        return int(settings.get("consult_budget", 3))
    except (TypeError, ValueError):
        return 3


__all__ = [
    "agent_loop_backend_name",
    "consult_budget",
    "consult_profiles",
    "find_consult_profile",
    "get_agent_loop_settings",
    "resolve_primary_profile",
]
