# KAGWeb Architecture — Framework Shell + Agent Loop

> What this fork keeps, what it removed, and where the conversation backend plugs in.

## Current shape

KAGWeb is a **framework shell** derived from DeepMentor: everything around the
conversation (providers, sessions, auth, settings, UI) is intact and tested.
The conversation backend itself is not a plain LLM call — it is an external
**agent loop**, selected at runtime:

```
CLI (kagweb_cli)   WebSocket /ws (/api/unified/ws)   Python SDK (KAGWebApp)
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
             claude-code · codex ·          intellect · intellect-team ·
             opencode · custom-cli          hermes · agentscope · custom-http
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
KAGWeb process. Presets `intellect`, `intellect-team`, `hermes`,
`agentscope`, `custom-http` all speak one small contract:

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

## Configuration

`data/user/settings/system.json`, `agent_loop` block (normalized by
`RuntimeSettingsService`; env overrides `KAGWEB_AGENT_LOOP_BACKEND` /
`_URL` / `_COMMAND`, and `KAG_AGENT_LOOP_API_KEY` — the credential carries
the KAG_ prefix because it belongs to the external agent service):

```json
{
  "backend": "claude-code",        // "" = shell stub
  "command": "",                   // CLI: override the preset executable
  "args": [],                      // CLI: extra argv ("{prompt}" placeholder)
  "env": {},                       // CLI: extra subprocess env
  "url": "",                       // HTTP: service base URL (required)
  "turn_path": "/agent/turn",      // HTTP: turn endpoint path
  "headers": {},                   // HTTP: extra headers
  "api_key": "",                   // HTTP: Bearer token
  "timeout_seconds": 900,          // per-turn wall clock (30..86400)
  "session_workspace": true        // CLI: per-session working dir
}
```

Misconfiguration (unknown preset, missing `command` / `url`) **fails turns**
with a localized error — never a silent fallback to the stub notice.

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

Tools mount through `ToolRegistry` (`kagweb/runtime/registry/tool_registry.py`);
the four user-toggleable tools (`brainstorm`, `web_search`, `paper_search`,
`reason`) live in `kagweb/tools/builtin/`.

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
| Turn coordination | `kagweb/runtime/` | multi-worker leader election, background commands (partners), memory reclaim |
| Multi-user | `kagweb/multi_user/` | grants, model/tool/partner access, guardians, learner profiles |
| Settings | `kagweb/services/config/` | model catalog (llm/task/search/tts/stt/imagegen), runtime settings, connection tests |
| Partners | `kagweb/partners/` + `kagweb/services/partners/` | IM channels (Telegram/Discord/Slack/Feishu/WeCom/Napcat/MS Teams/...), per-partner workspace & soul |
| Web | `web/` | Next.js, subpath-deployable (`NEXT_PUBLIC_BASE_PATH`), settings/multi-user/partners/space UIs |
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
