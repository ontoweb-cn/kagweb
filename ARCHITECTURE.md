# KAGWeb Architecture — Framework Shell + Agent Loop

> What this fork keeps, what it removed, and where the conversation backend plugs in.

## Current shape

KAGWeb is a **framework shell** derived from DeepMentor: everything around the
conversation (providers, sessions, auth, settings, UI) is intact and tested.
The conversation backend itself is not a plain LLM call — it is an external
**agent loop**, selected at runtime:

```
CLI (kagweb_cli)   WebSocket /ws   Python SDK (KAGWebApp)
        │                     │                            │
        └─────────────────────┼────────────────────────────┘
                              ▼
                    ChatOrchestrator (kagweb/runtime/orchestrator.py)
                              │  routes UnifiedContext by
                              │  context.active_capability or "chat"
                              ▼
                    CapabilityRegistry ──► ChatCapability
                              │            kagweb/capabilities/chat/
                              ▼
                    AgentLoopBackend (kagweb/services/agent_loop/)
                       │                                   │
                 CLI subprocess                       HTTP service
        claude-code · codex · opencode ·    intellect-team · hermes ·
        intellect (ACP) · custom-cli        agentscope · custom-http

        intellect runs `intellect acp` — one long-lived Agent Client
        Protocol child per session: message deltas, thinking, tool
        calls, plans, approvals (ask_user cards) and usage all map to
        neutral events; kagweb[acp] required.
                              │
                              ▼
                    neutral AgentLoopEvents → StreamBus → consumers
```

While no backend is configured (`agent_loop.backend == ""`), the chat
capability is the shell stub: every turn (CLI REPL, web chat, Partners, SDK)
completes normally with a localized notice.

## Two backend families

Both live under `kagweb/services/agent_loop/` and translate their vendor wire
format into the neutral `AgentLoopEvent` schema (`protocol.py`) — capabilities
and the frontend never see vendor-specific shapes.

### CLI family (`cli_backend.py`)

One subprocess per turn for terminal agent CLIs that emit NDJSON on stdout:

| Preset        | Default command | Mode                                   |
| ------------- | --------------- | -------------------------------------- |
| `claude-code` | `claude`        | `-p --output-format stream-json`       |
| `codex`       | `codex`         | `exec --json`                          |
| `opencode`    | `opencode`      | `run --json` (generic mapping)         |
| `custom-cli`  | from settings   | args may embed a `{prompt}` placeholder |

Vendor differences reduce to an argv template (`builtin.py`) plus a pure line
translator (`translate_claude_code` / `translate_codex` /
`translate_generic`). Lifecycle is shared and tested: prompt-on-argv, stdout
streaming, stderr draining, wall-clock timeout, kill-on-cancel, non-zero exit
→ failed turn with the stderr tail.

The child environment is **allowlisted** (PATH/HOME/TMPDIR plus the Windows
shell/temp/app-data basics) — the server environment carries deployment
secrets (`AUTH_PASSWORD_HASH`, `POCKETBASE_ADMIN_PASSWORD`, provider keys
are exported into it at boot) and must never reach the child. Backend
credentials pass exclusively through the operator's `env` settings block.
At most 4 agent-loop subprocesses run concurrently per process (each can be
a heavyweight node runtime); further turns wait, counting against their own
turn timeout.

With `session_workspace: true` (default) each session gets a working
directory (`data/user/workspace/chat/<session_id>`), so files the loop
creates stay reachable across its turns.

**Trust boundary**: the subprocess runs with the server process's
privileges (minus the exported secrets above). CLI backends are the
single-operator / local-deployment shape.

### HTTP family (`http_backend.py`)

One streaming POST per turn for agent services — the multi-user shape, since
the loop's code execution happens inside the operator's service, not the
KAGWeb process. `hermes`, `agentscope` and `custom-http` speak one small
contract; `intellect-team` and `intellect-runs` speak Intellect's run
channel instead (see below):

```
POST {url}{turn_path}                      # default path /agent/turn
Authorization: Bearer <api_key>            # when set
{"session_id": "…", "language": "en",
 "prompt": "user turn text",
 "history": [{"role": "user"|"assistant", "content": "…"}, …]}

→ 200: SSE (text/event-stream) or NDJSON, one JSON object per event:
  {"kind": "content"|"thinking"|"tool_call"|"tool_result"|"progress"|"usage"|"error",
   "text": "…", "name": "tool name", "data": {…}}
```

Objects without a neutral `kind` fall back to a heuristic translator, so a
service can adopt the contract gradually. Single lines (NDJSON and SSE data
events alike) are capped at `MAX_LINE_BYTES`; a runaway line is discarded
and the stream continues. HTTP errors and timeouts fail the turn with the
real status/body.

#### Intellect run channel (`protocol: "runs"`)

The `intellect-team` and `intellect-runs` presets speak Intellect's run
channel instead of the generic turn contract: `POST {url}/v1/runs` returns
`202 {run_id}` and `GET /v1/runs/{run_id}/events` streams lifecycle events
(SSE, each `data:` frame a `RunEvent`). Dispatch is on the payload's own
`type` first and the frame's `event` second — text, reasoning and the
completion frames all ride `event="message.delta"`, so reading `event`
alone silently drops every kind but the terminal ones.

Two properties of this channel shape the client:

- **The run outlives the subscription.** Its event queue is torn down when
  the SSE connection drops, and the server keeps working. A dropped stream
  therefore degrades to polling `GET /v1/runs/{run_id}` — whose terminal
  status carries the run's `output`, the only surviving copy of the answer
  — and any non-terminal exit (user cancel, consumer close, transport
  failure) must explicitly `POST /v1/runs/{run_id}/stop`, or the agent
  keeps working and spending on a turn nobody is waiting for.
- **The finished answer is reported twice** (as streamed deltas, then as
  `output`). Terminal frames reconcile against the last emitted block
  instead of appending, or a turn reads `"Hello world\n\nHello world"`; a
  truncated stream is completed by appending only the missing tail.

Control-plane windows are server-owned: the run channel resolves a pending
approval after 5 minutes and lets a pending clarify lapse after 2. Backends
declare this via `approval_timeout_limit` / `clarify_timeout_limit`, and the
capability clamps the operator's `approval_timeout_seconds` to them — a
longer client-side wait cannot extend the server's, it only guarantees the
answer arrives after the agent stopped listening.

### Identity: who a turn runs as

`identity_mode` (per profile, HTTP family) decides what a turn presents to
the agent service. The default `off` sends exactly what the profile
configures — the pre-existing behaviour.

| Mode | Sends | Effect |
| --- | --- | --- |
| `off` | profile `api_key` only | Nothing about the caller reaches the service |
| `header` | profile key + `X-Intellect-User: mem_<account>` | **Attribution**: the service records which account owns each session/run |
| `token` | the account's own linked member token | **Delegation**: service-side roles and per-owner isolation apply; unlinked users fall back to `header` |
| `token_required` | as `token`, but mandatory | An unlinked user cannot start a turn |

**`header` is attribution, not isolation.** The service key carries an
unrestricted principal (`bypass_member_filter`), so every ownership check
short-circuits: anything holding the key can reach any session or run by
id. KAGWeb's session store remains the isolation boundary, and the
per-account session-id namespace (`session_prefix`) keeps two accounts from
colliding on one remote session — a security control, not cosmetics.
Deployments that need the *service* to enforce separation must use
`token` / `token_required`.

**A broken link is a hard failure, never a downgrade.** If a linked
account's credential is expired, revoked, or rejected, the turn fails with
a message rather than silently falling back to the service key: that
fallback is *more* privileged than the token which just failed, so
downgrading would hand the user an escalation exactly when the restriction
started to matter. (An unlinked `token`-mode user may still be attributed —
a step up from sending nothing, which is a different situation.)

**A token is bound to the service it was minted against.** The profile URL
is admin-configurable, so a linked member token is only ever presented back
to the origin (scheme + host + port) it was created on. Otherwise repointing
the profile — a config edit, or selecting a different primary — would ship
every linked account's credential to the new host on its next turn. A link
whose origin no longer matches, or that predates the binding and records no
origin at all, is reported as needing reconnection rather than used.

Setting the password path aside from that: it is refused outright over
plaintext to a non-loopback host. A password is the user's own *reusable*
credential, unlike the revocable service key the operator chose to send, so
it is not put on the wire in the clear even where the deployment's own
requests are plaintext.

Linked credentials live at
`data/system/user-secrets/<owner>/private/intellect-agent/` (mode 0600,
outside every workspace so the sandbox's `exec` cannot read them), beside
the Codex OAuth store and in the same layout. The record names the KAGWeb
account it belongs to and is re-checked on read, so a copied or restored
file is ignored rather than lent to another account; deleting an account
purges the directory, which nothing else would do.

Per-user link management is deliberately **not** admin-gated (unlike
`/api/settings/agent-loop`): it is a personal credential, like the Codex
OAuth lifecycle. `GET/POST/DELETE /api/settings/agent-loop/identity` report
and manage the caller's own link and never echo the token; the card appears
under Settings → Models and hides itself when no agent service is
configured.

The two mutating routes — and the Codex OAuth start/cancel/logout — carry a
**same-origin guard** (`origin_is_trusted`). CORS does not cover this class
of attack: a cross-site JSON POST is *executed* by the server even when the
browser then refuses to return the response, and the request that matters is
the side effect. For a link endpoint the side effect is severe — binding a
victim to the *attacker's* identity would run the victim's conversations as
the attacker. The guard compares the request's own `Origin` against its
`Host` (scheme-insensitive, since TLS is normally terminated in front) and
against any explicitly configured CORS origin; `null` is never trusted, and
a configured `*` is deliberately not an exemption.

## Configuration

`data/user/settings/system.json`, `agent_loop` block (normalized by
`RuntimeSettingsService`; env overrides `KAGWEB_AGENT_LOOP_BACKEND` /
`_URL` / `_COMMAND`, and `KAG_AGENT_LOOP_API_KEY` — the credential carries
the KAG_ prefix because it belongs to the external agent service; env pins
land on the **primary** profile, creating a synthetic `env-override`
profile when the file configures none):

```json
{
  "version": 2,
  "profiles": [
    {
      "id": "default",              // stable id; the primary pointer uses it
      "name": "本地 Intellect",      // display label
      "preset": "intellect",        // backend kind (builtin.PRESETS);
                                    // community = ACP transport, team = HTTP
      "enabled": true,
      "command": "",                // CLI: override the preset executable
      "args": [],                   // CLI: extra argv ("{prompt}" placeholder)
      "env": {},                    // CLI: the ONLY credentials the child gets
      "session_workspace": true,    // CLI: per-session working dir
      "url": "http://localhost:8083", // HTTP: service base URL (required)
      "turn_path": "/agent/turn",   // HTTP: turn endpoint path
      "headers": {},                // HTTP: extra headers
      "api_key": "",                // HTTP: Bearer token
      "identity_mode": "off",       // HTTP: off | header | token | token_required
                                    // (see "Identity: who a turn runs as")
      "model": "",                  // model this backend should run
      "context_window": 0,          // real window for history budgeting; 0 = guess
      "timeout_seconds": 900,       // per-turn wall clock (30..86400)
      "consult_enabled": true       // may the primary consult this profile
    }
  ],
  "primary": "default",             // "" = shell stub; missing/null = auto
  "consult_budget": 3               // max consults per turn (0..12)
}
```

**Primary selection.** The primary drives every turn. A missing/null
`primary` auto-resolves: a local Intellect (community or enterprise)
first, then any local profile, then the first enabled one — so enabling a
local `intellect` / `intellect-team` makes it the primary by default. An
explicit `""` is the operator's shell-stub choice and is never overridden.
v1 flat blocks migrate into a single `"default"` profile on load.

**Detection** (`kagweb/services/agent_loop/detect.py`, exposed at
`GET /api/settings/agent-loop/detect`): CLI presets are PATH-probed with
`shutil.which` (Windows PATHEXT-safe) — the DeepMentor `detect_all()`
pattern — and configured HTTP profiles get a short direct reachability
GET (any HTTP response counts; no live agent turn is sent). ACP presets
additionally get a definitive handshake probe behind the settings Test
button (spawn → initialize → attach → shut down; no live turn). The
settings page probes on load and shows install/reachability badges.

**Approvals** (`AgentLoopEvent` kinds `approval_request` /
`clarify_request`, `protocol.APPROVAL_CHOICES`): control-capable backends
(`supports_control`, i.e. the ACP transport) may pause a turn awaiting a
user decision. The capability surfaces the request as an `ask_user` card
(web chips / CLI inline prompt), parks the turn through the runtime reply
queue for `approval_timeout_seconds`, and answers via
`backend.respond_approval` — falling back to the profile's
`approval_default` (default deny) on timeout or headless entry points.
Security note: setting `approval_default` to `once`/`session`/`always`
makes unattended entry points auto-approve tool runs — prefer `deny`
unless that is explicitly understood. ACP children are tracked per
session, capped at `MAX_ACTIVE_CHILDREN`, closed on API shutdown, and
swept by an atexit last resort.
Request-shaped events from backends without control support degrade to
progress notes.

Misconfiguration (unknown preset, missing `command` / `url`) **fails turns**
with a localized error — never a silent fallback to the stub notice.

The same block is configurable from the web UI under **Settings → Chat →
Agent Loop** (`/settings#agent-loop`, admin-only): profile cards (add /
edit / remove / enable), the primary picker with the automatic rule,
per-profile fields, detection badges, a no-live-turn configuration check,
and the API key as a write-only secret (`GET /api/settings/agent-loop`
redacts it; `PUT` treats an omitted key as "keep the stored one"). Changes
apply to the next turn — the chat capability re-reads the block and
rebuilds the backend per turn.

## Per-turn model selection

The composer's model picker (`llm_selection`) now drives the **conversation**
model, not just KAGWeb's own helper calls (titles/insights, which keep
working off the same selection). The grant-validated selection resolves to a
concrete model name on `AgentLoopRequest.model`; family support varies:

| Family | Support | Mechanism |
| --- | --- | --- |
| CLI (one-shot) | ✅ | `{model}` substitution uses the turn override, else the profile's `model`, else the arg drops |
| HTTP runs (`intellect-team` / `intellect-runs`) | ✅ | sent as `model` in the `POST /v1/runs` body, which both implementations read. The Python adapter validates it against its model catalog, so a name it does not know is rejected with `model_not_found` rather than ignored |
| HTTP turn (`hermes` / `agentscope` / `custom-http`) | ⬜ body carries `model` | honored where the service reads it; unknown services claim nothing |
| ACP | ❌ | no per-turn model field in the protocol; picker hidden |

`AgentLoopPreset.per_turn_model` declares support; `/api/auth/status` exposes
it as `model_selector_enabled` so the composer hides the picker when the
configured backend cannot honor it. Precedence: **turn selection >
profile `model` > backend default**.

## Consultation (multi agent-loop)

Enabled, `consult_enabled` profiles other than the primary are offered to
it for mid-turn consultation — DeepMentor's `consult_subagent` mechanism
adapted to KAGWeb's delegated-turn architecture (the primary is an
external process that cannot call KAGWeb tools, so the consult rides on
the existing request/response contract; see
`kagweb/services/agent_loop/consult.py`):

1. KAGWeb appends a **manifest** of consultable agents to the turn prompt.
2. A backend wanting a consultation ends its reply with **only** a fenced
   ```` ```consult {"agent": "<id>", "question": "…"} ``` ```` block.
3. KAGWeb parses the tail directive, runs the named profile (its events
   stream as progress under `consult:<name>`; the exchange surfaces as a
   `consult_agent` tool_call/tool_result pair), appends the result to the
   history, and re-runs the primary.
4. Repeat until no directive, the budget (`consult_budget`, default 3) is
   spent, or the directive repeats — then the last pass's answer is the
   turn's answer.

Consult sessions use `<chat session>::consult::<profile id>` session ids,
so backends with session state answer follow-up consults with continuity.
A consult failure (backend error, unknown agent reference) degrades to a
trace note — the turn still completes.

## Other integration seams

1. **Replace the delegation logic** — `ChatCapability.run()`
   (`kagweb/capabilities/chat/capability.py`) maps neutral events onto the
   StreamBus and assembles the persisted answer.
2. **Register additional capabilities** — add entries to
   `kagweb/runtime/bootstrap/builtin_capabilities.py`
   (`BUILTIN_CAPABILITY_CLASSES` / `BUILTIN_CAPABILITY_SPECS`) and drop the
   implementation under `kagweb/capabilities/<name>/`. The orchestrator, CLI
   routing (`kagweb run <capability>`), and the web capability catalog pick it
   up automatically.
3. **Add a backend preset** — extend `kagweb/services/agent_loop/builtin.py`
   (+ a translator for CLI shapes). No capability change needed.

There is no tool layer: built-in prompt-time tools and the MCP client stack
(`services/mcp/`, the `space_mcp`/`mcp_settings` routers, the
registry/provider plumbing, and the `mcp_tools` grant dimension) is removed —
the agent-loop backend carries its own tooling.

## Known boundaries

- **Bare turns**: with no LLM model configured, stub turns complete normally
  (`NoModelConfiguredError` → None config; the capability decides fatality).
  A model that is configured but broken fails the turn with its real error.
- **Non-admin users**: the turn gate still requires an LLM grant for
  non-admins (`request_preparer`). Pure agent-loop deployments without any
  granted model currently serve the admin/local user only; relaxing that is a
  product decision pending multi-user agent-loop grants.
- **Session-title generation** probes `has_configured_llm()` and falls back
  to truncating the first user message on bare deployments.

## Kept subsystems

| Layer | Location | Notes |
| --- | --- | --- |
| LLM providers | `kagweb/services/llm/` | OpenAI Chat Completions **and** Responses API wire protocols (`WireAPI = auto/responses/chat_completions`), Anthropic, Azure, Codex OAuth, Copilot, CodeBuddy, embedding-free |
| Sessions | `kagweb/services/session/` | SQLite + PocketBase stores, turn runtime (prepare/execute/lifecycle/title), request snapshots, regenerate |
| Turn coordination | `kagweb/runtime/` | multi-worker leader election, memory reclaim |
| Multi-user | `kagweb/multi_user/` | grants (models/exec/agent-loop), audit |
| Settings | `kagweb/services/config/` | model catalog (llm/task/search/tts/stt/imagegen), runtime settings, connection tests |
| Web | `web/` | Next.js, subpath-deployable (`NEXT_PUBLIC_BASE_PATH`), settings/multi-user/space UIs |
| Document parsing | `kagweb/services/parsing/` + `kagweb/utils/document_extractor.py` | chat attachments (PDF/Office/EPUB/text) — independent of the removed RAG layer |

## Removed subsystems (vs upstream DeepMentor 1.6.4)

- **Agent loop + capability graph**: `agents/` (chat loop, deep research,
  question generation, math animator, visualize, notebook, vision solver),
  the label-driven engine in `runtime/agentic/`, and the loop extensions
  (solve, mastery, reading, course study, watching, obsidian, marginnote4,
  ima, subagent, explore context, partner authoring/group, setup).
- **RAG / knowledge bases**: `knowledge/`, `services/rag/`, embedding
  service, GitHub/web source sync, the 66-endpoint knowledge router, and all
  KB UI. (`FileTypeRouter` survives as `kagweb/utils/file_types.py` because
  the attachment parser shares its extension tables.)
- **Partners / IM channels**: `partners/`, `services/partners/`,
  `services/partner_groups/`, the partner routers + WS endpoints, IM SDK
  extras, and all partner UI.
- **Persona**: `services/persona/` (presets + service), the `/api/personas`
  router, the turn `persona` field / `persona_context` injection, and the
  composer persona picker.
- **Learner + guardian**: `multi_user/{learner_profile,guardians,device_credentials}.py`,
  the learning-policy grant dimension, device-credential login, and the
  learner/guardian UI. Stored `preset: "learner"` rows degrade to
  `"standard"` on load.
- **Builtin tool package**: `tools/` (brainstorm/web_search/paper_search/
  reason, question bank, prompt-hint YAMLs) and the `enabled_tools` grant
  dimension. The turn contract lost its inert `tools` field, and legacy
  device-credential JWTs (carrying the removed `dcid`/`dcs` claims) are
  rejected at decode time, so any outstanding device session dies immediately
  instead of outliving the revocation path.
- **MCP client stack**: `services/mcp/` (manager, OAuth, catalog, secrets),
  the `space_mcp`/`mcp_settings` routers, the Space MCP page and admin
  registry UI, `runtime/providers/` + `ToolRegistry`/`ScopedToolRegistry`
  plumbing, `core/tool_protocol.py`, and the `mcp_tools` grant dimension.
  Registered tools had no delivery path to agent-loop backends (the turn
  contract carries no tool surface), so the stack was dormant behind a live
  configuration UI; removed whole on 2026-09-11.
- **Satellites**: memory, skills/EduHub, cron, sandbox execution, subagent
  (external CLI agent) harness, books, co-writer, notebooks, reading,
  video learning, visualizers, courses, CLI apps, videogen.
- Session-layer branches for the above (mastery leases, reading workspaces,
  course conventions, memory context, question bank context) were excised;
  legacy session rows containing those fields still load (fields are ignored
  or stripped by `workspace_preferences.upgrade_workspace_preferences`).

## Verification

```bash
KAGWEB_HOME=/tmp/kw-smoke python -m pytest tests          # backend
cd web && npm run typecheck && npm run test:node && npx vitest run
cd web && npm run build                                   # production build
```
