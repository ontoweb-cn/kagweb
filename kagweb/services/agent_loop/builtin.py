"""Built-in agent-loop backend presets.

A preset pins the transport family and the vendor-specific defaults; the
``agent_loop`` settings block overrides anything operator-configurable.
Two families:

* ``cli``  — terminal agent CLIs emitting NDJSON on stdout. The loop runs
  as a child of the KAGWeb process (server privileges; single-operator
  shape).
* ``http`` — agent services streaming SSE/NDJSON over POST (see
  :mod:`kagweb.services.agent_loop.http_backend` for the wire contract).
  The loop runs in the operator's own service; the multi-user shape.

Named HTTP presets (intellect / intellect-team / hermes / agentscope)
only provide defaults — the service side still speaks the documented
contract, or the operator adapts ``custom-http``.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class AgentLoopPreset:
    name: str
    family: str  # "cli" | "http"
    description: str
    # CLI family defaults.
    command: str = ""
    base_args: tuple[str, ...] = ()
    translator: str = "generic"
    # HTTP family defaults.
    turn_path: str = "/agent/turn"
    # Operators may always override these through settings.
    configurable: tuple[str, ...] = field(default=())


PRESETS: dict[str, AgentLoopPreset] = {
    preset.name: preset
    for preset in (
        AgentLoopPreset(
            name="claude-code",
            family="cli",
            description="Anthropic Claude Code CLI in stream-json print mode.",
            command="claude",
            base_args=("-p", "--output-format", "stream-json", "--verbose"),
            translator="claude-code",
        ),
        AgentLoopPreset(
            name="codex",
            family="cli",
            description="OpenAI Codex CLI in non-interactive JSON mode.",
            command="codex",
            base_args=("exec", "--json"),
            translator="codex",
        ),
        AgentLoopPreset(
            name="opencode",
            family="cli",
            description="OpenCode CLI agent in non-interactive JSON mode (generic mapping).",
            command="opencode",
            base_args=("run", "--json"),
            translator="generic",
        ),
        AgentLoopPreset(
            name="intellect",
            family="http",
            description="Intellect community edition agent service.",
        ),
        AgentLoopPreset(
            name="intellect-team",
            family="http",
            description="Intellect enterprise (team) agent service.",
        ),
        AgentLoopPreset(
            name="hermes",
            family="http",
            description="Hermes agent service.",
        ),
        AgentLoopPreset(
            name="agentscope",
            family="http",
            description="AgentScope agent service.",
        ),
        AgentLoopPreset(
            name="custom-cli",
            family="cli",
            description="Any CLI emitting NDJSON progress; command and args come from settings.",
        ),
        AgentLoopPreset(
            name="custom-http",
            family="http",
            description="Any service speaking the documented streaming turn contract.",
        ),
    )
}


__all__ = ["AgentLoopPreset", "PRESETS"]
