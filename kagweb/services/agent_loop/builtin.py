"""Built-in agent-loop backend presets.

A preset pins the vendor-specific defaults; the ``agent_loop`` settings
block overrides anything operator-configurable. Two families:

* ``cli``  — terminal agent CLIs emitting NDJSON on stdout. The loop runs
  as a child of the KAGWeb process (server privileges; single-operator
  shape).
* ``http`` — agent services streaming SSE/NDJSON over POST (see
  :mod:`kagweb.services.agent_loop.http_backend` for the wire contract).
  The loop runs in the operator's own service; the multi-user shape.

CLI presets come in two sub-transports: ``one-shot`` (a subprocess per turn,
NDJSON stdout) and ``acp`` (one long-lived Agent Client Protocol child per
session). Named HTTP presets (intellect-team / hermes / agentscope) only
provide defaults — the service side still speaks the documented contract, or
the operator adapts ``custom-http``.

A preset may offer **several transports** when one product has more than one
way to reach it: the community Intellect preset is reachable either through
its local ``intellect acp`` CLI child or through a remote ``/v1/runs``
service, and those two are different families with different privileges. The
profile's ``transport`` field selects one (empty = the preset's default), and
family-dependent behaviour resolves through :func:`resolve_transport` rather
than through the preset name alone.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class AgentLoopTransport:
    """One way of reaching a preset's agent, when a preset offers several.

    Single-transport presets are authored directly on :class:`AgentLoopPreset`;
    :func:`preset_transports` synthesizes their one entry so every caller can
    work with transports uniformly.
    """

    id: str  # "" for a preset's only transport; else "acp" | "http" | ...
    family: str  # "cli" | "http"
    label: str
    description: str
    # CLI family defaults.
    command: str = ""
    base_args: tuple[str, ...] = ()
    translator: str = "generic"
    # How the CLI family drives the child: ``one-shot`` spawns a subprocess
    # per turn (NDJSON stdout); ``acp`` keeps one long-lived Agent Client
    # Protocol child per session (see acp_backend.py).
    cli_transport: str = "one-shot"
    # HTTP family defaults.
    turn_path: str = "/agent/turn"
    # HTTP wire variant: ``turn`` posts a single streaming turn;
    # ``runs`` starts a run (202 + run_id), subscribes to its event stream
    # and answers approvals through the run control endpoints.
    protocol: str = "turn"
    # Optional preset-level reachability probe target (local health URL).
    probe_url: str = ""
    # Whether the user's per-turn model selection reaches this backend. True
    # only for one-shot CLI presets ({model} substitution is local); ACP has
    # no per-turn model field, and the HTTP presets stay False until their
    # services consume the body's ``model`` key.
    per_turn_model: bool = False


@dataclass(frozen=True)
class AgentLoopPreset:
    name: str
    family: str  # the default transport's family: "cli" | "http"
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
    # Whether the user's per-turn model selection reaches this backend. True
    # only for one-shot CLI presets ({model} substitution is local); ACP has
    # no per-turn model field, and the HTTP presets stay False until their
    # services consume the body's ``model`` key.
    per_turn_model: bool = False
    # Several transports for one product; empty = the preset fields above
    # describe its only transport.
    transports: tuple[AgentLoopTransport, ...] = ()
    #: Which transport a profile gets when it does not name one.
    default_transport: str = ""
    # Operators may always override these through settings.
    configurable: tuple[str, ...] = field(default=())


PRESETS: dict[str, AgentLoopPreset] = {
    preset.name: preset
    for preset in (
        AgentLoopPreset(
            name="claude-code",
            per_turn_model=True,
            family="cli",
            description="Anthropic Claude Code CLI in stream-json print mode.",
            command="claude",
            base_args=("-p", "--output-format", "stream-json", "--verbose"),
            translator="claude-code",
        ),
        AgentLoopPreset(
            name="codex",
            per_turn_model=True,
            family="cli",
            description="OpenAI Codex CLI in non-interactive JSON mode.",
            command="codex",
            base_args=("exec", "--json"),
            translator="codex",
        ),
        AgentLoopPreset(
            name="opencode",
            per_turn_model=True,
            family="cli",
            description="OpenCode CLI agent in non-interactive JSON mode (generic mapping).",
            command="opencode",
            base_args=("run", "--json"),
            translator="generic",
        ),
        AgentLoopPreset(
            name="intellect",
            # The community edition is reachable two ways; the local CLI
            # (ACP) stays the default, as it has been since the preset moved
            # off HTTP.
            family="cli",
            default_transport="acp",
            description=(
                "Intellect community edition: a local `intellect acp` child, "
                "or a remote agent service speaking its /v1/runs contract."
            ),
            transports=(
                AgentLoopTransport(
                    id="acp",
                    family="cli",
                    label="Local CLI (ACP)",
                    description=(
                        "Runs `intellect acp` on this host: full event stream "
                        "with approvals, plans and thinking. Requires the "
                        "`intellect` CLI and kagweb[acp]. The loop runs as a "
                        "local process with the server's privileges."
                    ),
                    command="intellect",
                    base_args=("acp",),
                    translator="",  # the ACP transport translates, not a line parser
                    cli_transport="acp",
                ),
                AgentLoopTransport(
                    id="http",
                    family="http",
                    label="HTTP service (/v1/runs)",
                    description=(
                        "Intellect community api_server over the run endpoints "
                        "(/v1/runs + SSE): deltas, tools, reasoning and approvals "
                        "for containerized deployments that cannot spawn the CLI."
                    ),
                    turn_path="/v1/runs",
                    protocol="runs",
                    probe_url="http://127.0.0.1:8642/health",
                    per_turn_model=True,
                ),
            ),
        ),
        AgentLoopPreset(
            name="intellect-team",
            family="http",
            description=(
                "Intellect enterprise (team) agent service over the run "
                "endpoints (/v1/runs + SSE). The event vocabulary is aligned to "
                "Intellect's Rust api_server (the authoritative implementation); "
                "answering a clarify needs that server, which exposes the "
                "response endpoint. **Requires the Rust api_server** — the "
                "legacy Python adapter names its tool and reasoning events "
                "differently, and those frames are dropped, so a turn against "
                "it produces no text. Supports approvals, clarifications and "
                "mid-turn cancellation."
            ),
            # The team deployment serves the same /health probe as the
            # community one; without it this preset had no probe target at all.
            probe_url="http://127.0.0.1:8642/health",
            turn_path="/v1/runs",
            protocol="runs",
            per_turn_model=True,
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
            per_turn_model=True,
            family="cli",
            description="Any CLI emitting NDJSON progress; command and args come from settings.",
        ),
        AgentLoopPreset(
            name="custom-http",
            family="http",
            description=(
                "Any service speaking KAGWeb's own streaming turn contract "
                "(neutral {kind:…} frames, or a vendor shape the translator "
                "understands). This is NOT an OpenAI chat-completions client: "
                "pointing it at /v1/chat/completions fails, because that "
                "endpoint carries no agent semantics. Use the service's agent "
                "endpoint instead — for Intellect that is /v1/runs."
            ),
        ),
    )
}


#: Preset names that identify the self-hosted Intellect services. Matched by
#: prefix rather than an enumeration so a new Intellect preset is covered
#: without another edit here (an enumerated set is what let `intellect-runs`
#: be missed when it was added).
_INTELLECT_PRESET_PREFIX = "intellect"


def _transport_from_preset(preset: AgentLoopPreset) -> AgentLoopTransport:
    """The single transport a plain preset describes."""

    return AgentLoopTransport(
        id="",
        family=preset.family,
        label=preset.name,
        description=preset.description,
        command=preset.command,
        base_args=preset.base_args,
        translator=preset.translator,
        cli_transport=preset.transport,
        turn_path=preset.turn_path,
        protocol=preset.protocol,
        probe_url=preset.probe_url,
        per_turn_model=preset.per_turn_model,
    )


def preset_transports(preset: str) -> tuple[AgentLoopTransport, ...]:
    """Every transport a preset offers (empty for an unknown preset)."""

    entry = PRESETS.get(str(preset or "").strip())
    if entry is None:
        return ()
    if entry.transports:
        return entry.transports
    return (_transport_from_preset(entry),)


def resolve_transport(preset: str, transport: str = "") -> AgentLoopTransport | None:
    """The transport a profile names, or ``None`` when it names nothing real.

    An empty *transport* resolves to the preset's default, which is what a
    profile stored before multi-transport presets existed carries. A non-empty
    id that the preset does not define is **not** silently defaulted here —
    callers decide, because ``build_agent_loop_backend`` must fail such a
    profile loudly while config normalization rewrites it to the default.
    """

    name = str(preset or "").strip()
    entry = PRESETS.get(name)
    if entry is None:
        return None
    transports = preset_transports(name)
    wanted = str(transport or "").strip().lower()
    if not wanted:
        wanted = entry.default_transport or transports[0].id
    for candidate in transports:
        if candidate.id == wanted:
            return candidate
    return None


def transport_key(preset: str, transport: str = "") -> str:
    """Stable key for one preset transport — the settings UI's detection key.

    Single-transport presets keep their bare preset name so existing
    detection payloads (``detects[presetName]``) stay valid; a preset with
    several transports keys each as ``<preset>:<transport>``.
    """

    entry = PRESETS.get(str(preset or "").strip())
    if entry is None:
        return str(preset or "").strip()
    transport_id = resolve_transport(preset, transport)
    if not entry.transports or transport_id is None or not transport_id.id:
        return entry.name
    return f"{entry.name}:{transport_id.id}"


def profile_transport_id(preset: str, transport: str = "") -> str:
    """The transport id to persist for a profile ("" when the preset has one).

    Resolved rather than passed through, so the stored file says which backend
    a profile builds instead of relying on the reader knowing today's default.
    An empty or unknown id lands on the default — the turn path still refuses
    an unknown id loudly, which is the right answer for a hand-edited file,
    but a value written by this build must never be one of those.
    """

    name = str(preset or "").strip()
    entry = PRESETS.get(name)
    if entry is None or not entry.transports:
        return ""
    resolved = resolve_transport(name, transport)
    if resolved is not None:
        return resolved.id
    return entry.default_transport or preset_transports(name)[0].id


def preset_family(preset: str, transport: str = "") -> str:
    """The transport family for a preset (``""`` when unknown).

    Without a *transport* this answers with the preset's default — the family
    of the backend a profile that names nothing else would build.
    """

    resolved = resolve_transport(preset, transport)
    return resolved.family if resolved is not None else ""


def is_intellect_preset(preset: str) -> bool:
    """Whether this preset name is one of the Intellect family.

    Used by the auto-primary rule, which prefers a local Intellect deployment:
    both editions and both HTTP/ACP transports are equally Intellect.
    """
    return str(preset or "").strip().startswith(_INTELLECT_PRESET_PREFIX)


def per_turn_model_apply(preset: str, transport: str = "") -> bool:
    """Whether the user's per-turn model selection reaches this backend.

    Mirrors the resolved transport's ``per_turn_model`` flag; an unknown
    preset or transport id never claims support.
    """
    resolved = resolve_transport(preset, transport)
    return bool(resolved.per_turn_model) if resolved is not None else False


def default_identity_mode(preset: str, transport: str = "") -> str:
    """The identity bridge a preset falls back to when a profile names none.

    The self-hosted Intellect services are the one family that gets attribution
    by default. A run against their api_server records an owner, and the sibling
    enterprise UI (AgentUI) always presents the signed-in account as
    ``X-Intellect-User`` — a KAGWeb deployment that sent nothing would file every
    turn under the unnamed service principal, which is the shape that later has
    to be cleaned up rather than the shape that works.

    Deliberately attribution only (``header``), matching AgentUI's data plane:
    the deployment key still authenticates and KAGWeb's own session store stays
    the isolation boundary. Enforcement is the operator's explicit choice —
    ``token`` / ``token_required`` present the user's own member token and need a
    link (Settings → Models), so they are never entered by default. A preset
    whose service knows nothing about ``X-Intellect-User`` keeps ``off``.
    """

    if is_intellect_preset(preset) and preset_family(preset, transport) == "http":
        return "header"
    return "off"


def llm_settings_apply(preset: str, transport: str = "") -> bool:
    """Whether the conversation-facing LLM settings apply to this backend.

    Only a **self-hosted HTTP** service needs conversation model credentials
    brought through KAGWeb. The CLI family — Claude Code, Codex, Intellect's
    own ACP transport — carries its own login state in the child's HOME, and a
    generic HTTP service configures its own models; presenting conversation
    profiles for those is the same misleading affordance as a model picker
    that has no effect on the conversation.

    Scoped to the conversation-LLM leaf only, deliberately: the rest of the
    models category applies under every backend. The ``task`` service feeds
    KAGWeb's own in-process calls (session titles, turn insights, history
    summaries) and the voice services feed ``/api/voice`` — none of those can
    borrow the CLI child's login, and the startup warning about a missing
    model points at them. Hiding the whole category used to bury the only
    page that warning's advice could act on.
    """

    resolved = resolve_transport(preset, transport)
    return is_intellect_preset(preset) and resolved is not None and resolved.family == "http"


__all__ = [
    "AgentLoopPreset",
    "AgentLoopTransport",
    "PRESETS",
    "default_identity_mode",
    "is_intellect_preset",
    "llm_settings_apply",
    "per_turn_model_apply",
    "preset_family",
    "preset_transports",
    "profile_transport_id",
    "resolve_transport",
    "transport_key",
]
