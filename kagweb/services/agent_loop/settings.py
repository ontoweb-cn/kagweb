"""Read the ``agent_loop`` block of the runtime system settings.

The block is persisted in ``data/user/settings/system.json`` and normalized
by :class:`kagweb.services.config.runtime_settings.RuntimeSettingsService`
(the defaults live there, next to the other blocks). This module gives the
agent-loop layer a single typed accessor so capabilities never touch the
raw settings dict.
"""

from __future__ import annotations

from typing import Any

#: Settings shape (defaults defined in runtime_settings.DEFAULT_SYSTEM_SETTINGS):
#:
#:   "agent_loop": {
#:     "backend": "",             # preset name; "" disables the layer (stub chat)
#:     "command": "",             # CLI family: override the preset's executable
#:     "args": [],                # CLI family: extra argv appended to the preset's
#:     "env": {},                 # CLI family: extra env vars — the ONLY credentials
#:                                #   the child gets; it does NOT inherit the server env
#:     "url": "",                 # HTTP family: base URL of the agent service
#:     "turn_path": "/agent/turn",  # HTTP family: path of the streaming turn endpoint
#:     "headers": {},             # HTTP family: extra headers (auth schemes etc.)
#:     "api_key": "",             # HTTP family: sent as Authorization: Bearer …
#:     "timeout_seconds": 900,    # per-turn wall clock cap, both families
#:     "session_workspace": True, # CLI family: per-session working directory
#:   }
#:


def get_agent_loop_settings() -> dict[str, Any]:
    """Return the normalized ``agent_loop`` settings block."""
    from kagweb.services.config.runtime_settings import RuntimeSettingsService

    system = RuntimeSettingsService.get_instance().load_system()
    block = system.get("agent_loop")
    return dict(block) if isinstance(block, dict) else {}


def agent_loop_backend_name() -> str:
    """Return the configured backend preset name ("" when unset)."""
    return str(get_agent_loop_settings().get("backend") or "").strip()


__all__ = [
    "agent_loop_backend_name",
    "get_agent_loop_settings",
]
