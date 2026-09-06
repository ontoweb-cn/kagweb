# KAGWeb — Agent-Native Architecture

## Overview

KAGWeb is an **agent-native** framework organized around a two-layer plugin
model — single-shot **Tools** invoked by the LLM, and multi-stage
**Capabilities** that take over a turn — exposed through three entry points:
CLI, WebSocket API, and Python SDK.

This fork ships a **framework shell**: the runtime, provider, storage, and web
layers are complete. The conversation backend is an external **agent loop** —
not a plain LLM call — selected at runtime via the `agent_loop` settings block
(`kagweb/services/agent_loop/`): CLI backends (Claude Code, Codex, OpenCode,
custom) and HTTP service backends (Intellect community/team, Hermes,
AgentScope, custom). While no backend is configured, `chat` is a stub that
completes every turn with a localized notice. See `ARCHITECTURE.md` for the
wire contracts and trust boundaries.

## Architecture

```
Entry Points:  CLI (Typer)  |  WebSocket /ws  |  Python SDK
                    ↓                   ↓                   ↓
              ┌─────────────────────────────────────────────────┐
              │                ChatOrchestrator                 │
              │   routes UnifiedContext -> selected Capability  │
              │   (defaults to `chat`)                          │
              └──────────┬──────────────┬───────────────────────┘
                         │              │
              ┌──────────▼──┐  ┌────────▼──────────┐
              │ ToolRegistry │  │ CapabilityRegistry │
              │  (Level 1)   │  │   (Level 2)        │
              └──────────────┘  └────────┬──────────┘
                                          │ chat delegates to
                              ┌───────────▼───────────┐
                              │  AgentLoopBackend     │
                              │  CLI subprocess | HTTP │
                              └───────────────────────┘
```

All capabilities emit on a shared `StreamBus`; the orchestrator fans events out
to consumers. Runtime settings live in `data/user/settings/*.json` —
project-root `.env` files are intentionally ignored.

### Level 1 — Tools

Single-function tools the user can toggle in `/settings/tools`:

| Tool           | Description                                   |
| -------------- | --------------------------------------------- |
| `brainstorm`   | Breadth-first idea exploration with rationale |
| `web_search`   | Web search with citations                     |
| `paper_search` | arXiv preprint search                         |
| `reason`       | Dedicated deep-reasoning LLM call             |

`CONFIGURABLE_BUILTIN_TOOL_NAMES` (`kagweb/tools/builtin/__init__.py`) is the
mount point for future context-gated tools.

### Level 2 — Capabilities

`chat` is the only built-in capability (`kagweb/capabilities/chat/`): with an
agent-loop backend configured it delegates the turn and maps the backend's
neutral events onto the `StreamBus`; otherwise it is the shell stub. All
capabilities converge on `emit_capability_result()` in
`kagweb/capabilities/_shared.py` so every turn emits the same envelope
(response payload + `cost_summary` from `UsageTracker`).

## CLI Usage

```bash
# Install
pip install kagweb      # Full app (CLI + Web/API + packaged Web assets)
pip install kagweb-cli  # CLI-only

# Run the chat capability (stub notice without an agent_loop backend)
kagweb run chat "Explain Fourier transform"

# Interactive REPL
kagweb chat

# Partners (IM-connected companions)
kagweb partner list

# Server
kagweb serve --port 8001       # API server only
kagweb start                   # backend + frontend together
```

## Key Files

| Path                                       | Purpose                              |
| ------------------------------------------ | ------------------------------------ |
| `kagweb/runtime/orchestrator.py`           | `ChatOrchestrator` — unified entry   |
| `kagweb/runtime/launcher.py`               | Backend + frontend lifecycle / port discovery |
| `kagweb/runtime/registry/`                 | Tool + Capability registries         |
| `kagweb/runtime/bootstrap/builtin_capabilities.py` | Built-in capability class paths |
| `kagweb/services/config/runtime_settings.py` | JSON settings + process-env overrides |
| `kagweb/core/stream.py`, `stream_bus.py`   | StreamEvent protocol + async fan-out |
| `kagweb/core/tool_protocol.py`             | `BaseTool` + `ToolDefinition`        |
| `kagweb/core/capability_protocol.py`       | `TurnCapability` + `CapabilityManifest` |
| `kagweb/core/context.py`                   | `UnifiedContext` dataclass           |
| `kagweb/tools/builtin/__init__.py`         | Built-in tool wrappers               |
| `kagweb/capabilities/`                     | Built-in capability implementations  |
| `kagweb/services/agent_loop/`              | Agent-loop backends (CLI/HTTP) + presets |
| `kagweb/app.py`                            | `KAGWebApp` — Python SDK facade      |
| `kagweb_cli/main.py`                       | Typer CLI entry point                |
| `kagweb/api/routers/unified_ws.py`         | Unified WebSocket endpoint           |

## LLM Providers

`kagweb/services/llm/provider_core/` keeps the full provider stack. Endpoints
speak either wire protocol — OpenAI **Chat Completions**
(`/v1/chat/completions`) or the newer **Responses API** (`/v1/responses`) —
selected per provider via `WireAPI` (`auto` by default: Chat Completions
everywhere, Responses for OpenAI reasoning models, with a circuit breaker and
fallback).

## Dependency Layers

Public install paths and source extras are defined in `pyproject.toml`.

```
pip install kagweb      — Full app (CLI + Web/API + packaged Web assets)
pip install kagweb-cli  — CLI-only (LLM + providers + document parsing)
pip install -e .        — Source install for development

Source extras (.[extra]):
.[cli]            — CLI-only dependency set
.[server]         — Web/API server dependencies
.[partners]       — Partner channel SDKs
.[matrix]         — Matrix channel (matrix-nio; needs libolm)
.[matrix-e2e]     — Matrix with end-to-end encryption
.[dev]            — Test / lint tooling
.[all]            — Everything above
```

## Web Rule: Subpath Deployment

The web app must stay deployable under a **subpath** (e.g.
`https://your.host/kagweb` behind Kubernetes/Ingress), not only at a domain
root. When writing code under `web/`, never assume the app is served from `/`:

- API / WS / SSE URLs — build them via `apiUrl()` / `wsUrl()`
  (`web/shared/api/client.ts`) so a basePath can be applied centrally. Do not
  call `fetch` / `new WebSocket` / `new EventSource` with raw `"/api/…"` /
  `"/ws/…"` strings.
- Auth redirects — go through `loginHref()` / `normalizeInternalReturnPath()`
  (`web/shared/auth/return-url.ts`) and the path constants in
  `web/lib/proxy-policy.ts`; keep `proxy.ts` and `isBackendPath()` prefix-aware.
- Raw HTML asset references (e.g. `<svg><image href="/logo.png">`) bypass
  Next's basePath handling — use `next/image` or a helper instead.
- `Link` / `router.push` destinations are app-relative paths and are
  basePath-prefixed by the framework — never prepend the domain or a
  deployment prefix by hand.
