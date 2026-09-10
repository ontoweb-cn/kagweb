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

CLI presets come in two transports: ``one-shot`` (a subprocess per turn,
NDJSON stdout) and ``acp`` (one long-lived Agent Client Protocol child per
session — the community ``intellect`` preset). Named HTTP presets
(intellect-team / hermes / agentscope) only provide defaults — the service
side still speaks the documented contract, or the operator adapts
``custom-http``.
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
    # How the CLI family drives the child: ``one-shot`` spawns a subprocess
    # per turn (NDJSON stdout); ``acp`` keeps one long-lived Agent Client
    # Protocol child per session (see acp_backend.py).
    transport: str = "one-shot"
    # HTTP family defaults.
    turn_path: str = "/agent/turn"
    # HTTP wire variant: ``turn`` posts a single streaming turn;
    # ``runs`` starts a run (202 + run_id), subscribes to its event stream
    # and answers approvals through the run control endpoints.
    protocol: str = "turn"
    # Optional preset-level reachability probe target (local health URL).
    probe_url: str = ""
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
            family="cli",
            description=(
                "Intellect community edition over the Agent Client Protocol "
                "(`intellect acp`): full event stream with approvals, plans "
                "and thinking. Requires the `intellect` CLI and kagweb[acp]."
            ),
            command="intellect",
            base_args=("acp",),
            translator="",  # the ACP transport translates, not a line parser
            transport="acp",
        ),
        AgentLoopPreset(
            name="intellect-team",
            family="http",
            description=(
                "Intellect enterprise (team) agent service over the run "
                "endpoints (/v1/runs + SSE). The event vocabulary is aligned to "
                "Intellect's Rust api_server (the authoritative implementation); "
                "answering a clarify needs that server, which exposes the "
                "response endpoint."
            ),
            turn_path="/v1/runs",
            protocol="runs",
        ),
        AgentLoopPreset(
            name="intellect-runs",
            family="http",
            description=(
                "Intellect community api_server over the run endpoints "
                "(/v1/runs + SSE): deltas, tools, reasoning and approvals "
                "for containerized deployments that cannot spawn the CLI."
            ),
            turn_path="/v1/runs",
            protocol="runs",
            probe_url="http://127.0.0.1:8642/health",
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


#: Preset names that identify the self-hosted Intellect services. Matched by
#: prefix rather than an enumeration so a new Intellect preset is covered
#: without another edit here (an enumerated set is what let `intellect-runs`
#: be missed when it was added).
_INTELLECT_PRESET_PREFIX = "intellect"


def preset_family(preset: str) -> str:
    """The transport family for a preset name (``""`` when unknown)."""
    entry = PRESETS.get(str(preset or "").strip())
    return entry.family if entry is not None else ""


def is_intellect_preset(preset: str) -> bool:
    """Whether this preset name is one of the Intellect family.

    Used by the auto-primary rule, which prefers a local Intellect deployment:
    both editions and both HTTP/ACP transports are equally Intellect.
    """
    return str(preset or "").strip().startswith(_INTELLECT_PRESET_PREFIX)


def llm_settings_apply(preset: str) -> bool:
    """Whether the LLM (models and connections) settings apply to this backend.

    Only a **self-hosted HTTP** service needs the operator to bring model
    credentials through KAGWeb. The CLI family — Claude Code, Codex, Intellect's
    own ACP transport — carries its own login state in the child's HOME, and a
    generic HTTP service configures its own models; presenting the section for
    those is the same misleading affordance as a model picker that has no
    effect on the conversation.
    """
    name = str(preset or "").strip()
    return is_intellect_preset(name) and preset_family(name) == "http"


__all__ = [
    "AgentLoopPreset",
    "PRESETS",
    "is_intellect_preset",
    "llm_settings_apply",
    "preset_family",
]
