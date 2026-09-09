"""Agent-loop conversation backends — the KAGWeb chat seam.

``get_agent_loop_backend()`` resolves the runtime-configured backend
(system settings ``agent_loop`` block, see
:mod:`kagweb.services.agent_loop.settings`) or returns ``None`` when the
conversation backend is not configured — in which case the chat
capability stays the framework-shell stub. Misconfiguration (unknown
preset, missing URL / command) raises :class:`AgentLoopError` so the
operator sees the real problem instead of a silently stubbed chat.
"""

from __future__ import annotations

import logging

from kagweb.services.i18n import t

from .builtin import PRESETS
from .cli_backend import TRANSLATORS, CliAgentLoopBackend, translate_generic
from .http_backend import HttpAgentLoopBackend
from .protocol import (
    AgentLoopBackend,
    AgentLoopError,
    AgentLoopEvent,
    AgentLoopRequest,
)

logger = logging.getLogger(__name__)


def build_agent_loop_backend(settings: dict | None = None) -> AgentLoopBackend | None:
    """Instantiate one agent-loop backend from a profile dict; ``None`` when
    no preset is configured.

    Accepts a v2 profile dict (``preset`` key) or a legacy flat block
    (``backend`` key) so tests and older callers keep working. Raises
    :class:`AgentLoopError` for a configured-but-unusable backend (unknown
    preset, missing CLI command, missing HTTP URL) — those must surface as
    failed turns, never fall back to the stub notice.
    """
    resolved = dict(settings if settings is not None else {})
    name = str(resolved.get("preset") or resolved.get("backend") or "").strip()
    if not name:
        return None
    preset = PRESETS.get(name)
    if preset is None:
        raise AgentLoopError(t("agent_loop.unknown_backend", backend=name), backend=name)

    timeout = float(resolved.get("timeout_seconds") or 0.0)
    if preset.family == "cli":
        command = str(resolved.get("command") or "").strip() or preset.command
        if not command:
            raise AgentLoopError(t("agent_loop.command_required", backend=name), backend=name)
        extra_args = [str(arg) for arg in (resolved.get("args") or [])]
        env = resolved.get("env") if isinstance(resolved.get("env"), dict) else {}
        if getattr(preset, "transport", "one-shot") == "acp":
            # Lazy import: the ACP SDK is an extra (kagweb[acp]); the backend
            # itself is importable and only the spawn path needs the SDK.
            from .acp_backend import AcpAgentLoopBackend

            return AcpAgentLoopBackend(
                name=name,
                command=command,
                base_args=list(preset.base_args),
                env=env,
                timeout_seconds=timeout,
            )
        return CliAgentLoopBackend(
            name=name,
            command=command,
            base_args=list(preset.base_args),
            extra_args=extra_args,
            env=env,
            timeout_seconds=timeout,
            translator=TRANSLATORS.get(preset.translator, translate_generic),
            text_output=bool(resolved.get("text_output")),
        )

    url = str(resolved.get("url") or "").strip()
    if not url:
        raise AgentLoopError(t("agent_loop.url_required", backend=name), backend=name)
    turn_path = str(resolved.get("turn_path") or "").strip() or preset.turn_path
    headers = resolved.get("headers") if isinstance(resolved.get("headers"), dict) else {}
    if getattr(preset, "protocol", "turn") == "runs":
        from .http_backend import RunsAgentLoopBackend

        return RunsAgentLoopBackend(
            name=name,
            url=url,
            turn_path=turn_path,
            api_key=str(resolved.get("api_key") or ""),
            headers=headers,
            timeout_seconds=timeout,
        )
    return HttpAgentLoopBackend(
        name=name,
        url=url,
        turn_path=turn_path,
        api_key=str(resolved.get("api_key") or ""),
        headers=headers,
        timeout_seconds=timeout,
    )


def get_agent_loop_backend() -> AgentLoopBackend | None:
    """Resolve the deployment's configured agent-loop backend per turn."""
    return build_agent_loop_backend()


__all__ = [
    "AgentLoopBackend",
    "AgentLoopError",
    "AgentLoopEvent",
    "AgentLoopRequest",
    "build_agent_loop_backend",
    "get_agent_loop_backend",
]
