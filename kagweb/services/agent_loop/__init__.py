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

from .builtin import PRESETS, normalize_profile_models, resolve_transport
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

    # Which way to reach this preset's agent. A profile that names a transport
    # the preset does not define is a hard error: silently building the default
    # would run the turn somewhere the operator did not ask for — and for the
    # community Intellect preset that difference is "local child process with
    # server privileges" versus "remote HTTP service".
    requested_transport = str(resolved.get("transport") or "").strip()
    transport = resolve_transport(name, requested_transport)
    if transport is None:
        raise AgentLoopError(
            t("agent_loop.unknown_transport", backend=name, transport=requested_transport),
            backend=name,
        )

    timeout = float(resolved.get("timeout_seconds") or 0.0)
    #: The operator's chosen model, if any. Empty means "use the backend's own
    #: default" and leaves every code path exactly as it was without this field.
    model = str(resolved.get("model") or "").strip()
    #: The operator-curated per-turn model vocabulary ([{id, name}]). Normalized
    #: here so the backends see one shape whatever the settings file carried.
    models = normalize_profile_models(resolved.get("models"))
    if transport.family == "cli":
        command = str(resolved.get("command") or "").strip() or transport.command
        if not command:
            raise AgentLoopError(t("agent_loop.command_required", backend=name), backend=name)
        extra_args = [str(arg) for arg in (resolved.get("args") or [])]
        env = resolved.get("env") if isinstance(resolved.get("env"), dict) else {}
        if transport.cli_transport == "acp":
            # Lazy import: the ACP SDK is an extra (kagweb[acp]); the backend
            # itself is importable and only the spawn path needs the SDK.
            from .acp_backend import AcpAgentLoopBackend

            return AcpAgentLoopBackend(
                name=name,
                command=command,
                base_args=list(transport.base_args),
                env=env,
                timeout_seconds=timeout,
                model=model,
                models=models,
            )
        return CliAgentLoopBackend(
            name=name,
            command=command,
            base_args=list(transport.base_args),
            extra_args=extra_args,
            env=env,
            timeout_seconds=timeout,
            translator=TRANSLATORS.get(transport.translator, translate_generic),
            text_output=bool(resolved.get("text_output")),
            model=model,
            models=models,
        )

    url = str(resolved.get("url") or "").strip()
    if not url:
        raise AgentLoopError(t("agent_loop.url_required", backend=name), backend=name)
    turn_path = str(resolved.get("turn_path") or "").strip() or transport.turn_path
    headers = resolved.get("headers") if isinstance(resolved.get("headers"), dict) else {}
    api_key = str(resolved.get("api_key") or "")
    # `identity_mode` rides along unresolved: *who the turn runs as* is per-user
    # and only knowable inside a request, so the capability applies it via
    # `with_identity`. Resolving it here would make merely building a backend
    # depend on the current account (and fail the settings Test action, which
    # builds a profile precisely to probe it), and this function is deployment
    # configuration, not turn configuration.
    identity_mode = str(resolved.get("identity_mode") or "off")
    # The instance tenant the service runs as (Intellect: a 32-hex id the
    # service validates against its own configured tenant). Deployment
    # configuration, applied to every request the backend makes, so it is
    # threaded here rather than resolved per turn like identity.
    tenant_id = str(resolved.get("tenant_id") or "").strip()
    if transport.protocol == "runs":
        from .http_backend import RunsAgentLoopBackend

        return RunsAgentLoopBackend(
            name=name,
            url=url,
            turn_path=turn_path,
            api_key=api_key,
            headers=headers,
            timeout_seconds=timeout,
            model=model,
            models=models,
            identity_mode=identity_mode,
            tenant_id=tenant_id,
        )
    return HttpAgentLoopBackend(
        name=name,
        url=url,
        turn_path=turn_path,
        api_key=api_key,
        headers=headers,
        timeout_seconds=timeout,
        model=model,
        models=models,
        identity_mode=identity_mode,
        tenant_id=tenant_id,
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
