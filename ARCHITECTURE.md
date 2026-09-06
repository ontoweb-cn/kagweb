# KAGWeb Architecture — Framework Shell

> What this fork keeps, what it removed, and where the KAG backend plugs in.

## Current shape

KAGWeb is a **framework shell** derived from DeepMentor: everything around the
conversation (providers, sessions, auth, settings, UI) is intact and tested;
the conversation implementation itself is a stub.

```
CLI (kagweb_cli)   WebSocket /ws (/api/unified/ws)   Python SDK (KAGWebApp)
        │                     │                            │
        └─────────────────────┼────────────────────────────┘
                              ▼
                    ChatOrchestrator (kagweb/runtime/orchestrator.py)
                              │  routes UnifiedContext by
                              │  context.active_capability or "chat"
                              ▼
                    CapabilityRegistry ──► ChatCapability (STUB)
                              │            kagweb/capabilities/chat/
                              ▼
                          StreamBus ──► consumers (WS, session recorder)
```

## What the stub does

`ChatCapability.run()` streams one localized notice (en/zh, from
`kagweb/services/i18n.py` key `chat.stub_notice`), publishes it as the turn's
`capability_output`, and emits the standard `result` + `done` events. Every
surface (CLI REPL, web chat, Partners IM, SDK) completes a normal turn.

## KAG backend integration point

Two supported seams, both in place and tested:

1. **Replace the stub body** — implement `ChatCapability.run()`
   (`kagweb/capabilities/chat/capability.py`). You receive a
   `UnifiedContext` (user message, history, attachments, persona, LLM
   selection) and a `StreamBus` to stream on. Emit
   `content` / `tool_call` / `result` events; persist via
   `context.capability_output`.
2. **Register additional capabilities** — add entries to
   `kagweb/runtime/bootstrap/builtin_capabilities.py`
   (`BUILTIN_CAPABILITY_CLASSES` / `BUILTIN_CAPABILITY_SPECS`) and drop the
   implementation under `kagweb/capabilities/<name>/`. The orchestrator, CLI
   routing (`kagweb run <capability>`), and the web capability catalog pick it
   up automatically.

Tools mount through `ToolRegistry` (`kagweb/runtime/registry/tool_registry.py`);
the four user-toggleable tools (`brainstorm`, `web_search`, `paper_search`,
`reason`) live in `kagweb/tools/builtin/`.

## Kept subsystems

| Layer | Location | Notes |
|---|---|---|
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
KAGWEB_HOME=/tmp/kw-smoke python -m pytest tests          # backend (2044 tests)
cd web && npm run typecheck && npm run test:node && npx vitest run
cd web && npm run build                                   # production build
```
