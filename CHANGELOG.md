# Changelog

All notable changes to KAGWeb are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
Entries are grouped from the repository's [Conventional Commits](CONTRIBUTING.md#commit-message-format);
`!` marks a breaking change.

**Release mechanics.** Bump `kagweb/__version__.py`, commit, then tag `v<version>`.
Publishing is triggered by *publishing a GitHub Release* (not by pushing the tag):
`.github/workflows/pypi-release.yml` and `docker-release.yml` verify that the tag
matches `kagweb/__version__.py` before publishing. Tags `v0.2.0` and earlier were
never cut.

## [Unreleased]

### Documentation

- Re-baseline `docs/backend-architecture.md` and `docs/backend-llm-deployment.md`
  to the v0.2.2 source tree, marking every conclusion invalidated by the batch
  4/5 removals and the P0/P1 fixes.
- Add `docs/agentui-kagweb-merge-feasibility.md`: a static feasibility assessment
  of selectively migrating AgentUI features (knowledge-base management, workflow
  canvas, agent-facing UI) into KAGWeb as the host.

## [0.2.2] — 2026-09-20

The release that turns the framework shell into a working system: the shell-only
`chat` capability is replaced by real agent-loop backends, the KAG integration is
built end to end, and the inherited product layer is removed. 174 commits;
718 files changed (+49,203 / −111,809).

### Added

**KAG integration**

- `kag-bridge`: an MCP server exposing KAG reasoning as agent-loop tools, with
  streamable-HTTP transport and bearer auth, and an optional `kag_reason` DSL
  tool. Packaged separately under `kag-bridge/`.
- `kagweb/services/kag/`: OpenSPG client, task store, schema draft, member store,
  access checks, and trace folding into session activity metadata.
- `/api/kag` management API — projects, schema read/alter, members, graph query,
  build trigger, builds/tasks — plus `/api/kag/bridge` for service-to-service
  task reporting (api_key auth, no JWT).
- Web management console: project list/detail, schema relation editing, graph
  browsing over the reason DSL on a Cytoscape canvas, build-task observability
  with live status and node logs, and a guided `KAG_COMMAND` data import panel.
- Per-session bridge tokens, keeping the instance key out of session workdirs.
- Multi-user project ACL with `userNo` attribution.

**Agent-loop backends**

- Intellect community edition over the Agent Client Protocol (ACP), running as a
  long-lived child process.
- `intellect-team` over the `/v1/runs` contract, aligned to Intellect's Rust
  `api_server` as the authoritative implementation.
- Per-turn model selection reaching the backend, with a profile-level `model`
  and `context_window` (CLI `{model}` substitution, HTTP request-body key).
- Approval requests surfaced as `ask_user`-shaped cards on both client channels,
  resolved back through the UI; clarify questions carried to the user and back
  (ACP and runs).
- Native session resume for one-shot CLI presets, a universal transcript file
  for long sessions, ACP reset recovery, and opencode two-turn continuity.
- Text-output mode for one-shot CLIs that emit plain text instead of NDJSON.
- Multi-user identity bridge: `identity_mode` of `off` / `header` / `token` /
  `token_required`, mapping a KAGWeb account onto an Intellect subject.

**Access control**

- `CapabilityManifest.required_service` replaces the hardcoded LLM grant, so a
  turn is gated on the resource it actually needs.
- New `agent_loop` (default-allow, HTTP family) and `agent_loop_cli` (opt-in, CLI
  and ACP families — driving one is code execution as the server user) grants.

**Web UI**

- Settings navigation restructured: each category (`appearance`, `network`,
  `agent-loop`, `models`, `knowledge`, `chat`, `kag`, `about`) routes to its own
  page instead of one fragment-addressed document, and the agent backend becomes
  a top-level section with LLM settings gated to backends that need them.
- Trace three-tier disclosure with observation excerpts and a learner/expert
  switch; DAG semantic zoom and thought-map export.
- Post-turn insight badge for multi-round explorations, truncated-turn marking,
  rejected-submit feedback, `ask_user` draft rendering, CJK emphasis repair.
- Rebrand to KAG WebUI with the ONTOWEB badge logo.

**CLI / sessions**

- `kagweb restart`, plus the download-and-run flow in the docs.
- Session deletion reclaims workspace artifacts.
- Attachments are materialized into the session workspace with manifest paths,
  closing the "attachment full text never reaches the backend" gap.

### Changed

- Default `chat` capability delegates to the configured agent-loop backend
  instead of completing every turn with a shell notice.

### Removed

Breaking removals — the inherited product layer. Each was a deliberate decision
recorded in `docs/backend-llm-deployment.md` §5, not drift.

- **Partners / IM-channel subsystem** (`313a64a`) — 174 files, −56,069 lines:
  channel layer, runtime, partner groups, 66 API endpoints, web routes, CLI
  command.
- **MCP client stack** (`c20bb10`) — 93 files, −15,765 lines: `services/mcp/`,
  `runtime/providers/`, `ToolRegistry`/`ScopedToolRegistry`, `core/tool_protocol.py`,
  the `mcp_tools` grant dimension.
- **Persona subsystem and builtin skill packages** (`77bd9b6`) — 50 files,
  −4,133 lines.
- **Builtin tool package** (`ef8bbf5`) — 83 files, −3,026 lines, together with
  the learning-era naming.
- **Learner and guardian subsystems**, the quiz/notebook data surface, and the
  `cli_apps` grant dimension (designed but never built).
- **`codex_auth` and the `openai_codex` provider** — the in-process Codex
  credential chain and its OAuth card. The `codex` CLI preset is unaffected
  (it reads `~/.codex` itself). A stale `openai_codex` profile in an existing
  catalog stays owner-bound, i.e. never grantable.
- Builtin `SKILL.md` assets and the `**/*.md` package-data rule.

### Fixed

- The access gate now runs on every non-admin turn rather than only when the
  caller left `llm_selection` unset — pinning that client-supplied field could
  otherwise skip the resource check.
- Profile family is derived from the preset registry; the attachment
  materialization gate had been reading a key that never existed, so it never
  opened in a real CLI deployment.
- Token counters no longer fabricated or dropped; duration and pass-level
  counters are backend-authoritative.
- Intellect runs channel aligned with the Rust contract (text, reasoning, tool,
  approval and clarify frames were silently dropped).
- `regenerate` was broken since the tools-contract removal; backend-native
  selections restored.
- Regenerate, ask_user and DAG state-ring honesty fixes across the web client.
- `kagweb run` no longer passes the retired `--tool` flag.

### Security

- Legacy device tokens expire.
- Remote resource ids are kept inside their URL path segment; event bodies are no
  longer logged.
- Intellect tokens are bound to the service origin that issued them, so changing
  a profile URL cannot exfiltrate a user's token.
- Cross-site requests to credential-establishing endpoints are rejected via an
  `Origin` check (independent of CORS); `Origin: null` is refused.
- `member_id` folding collisions fixed — folded ids now disambiguate with a
  digest, so two accounts cannot be attributed to one subject.
- Attachment workspace copies harden against hostile attachment ids.
- The intranet instance key no longer reaches per-session workdirs.

## [0.2.1] — 2026-09-09

### Added

- CLI profiles can run in an operator-chosen directory.

### Fixed

- Agent-loop turns render with real tool rows and reasoning in the web client.
- Paragraphs are preserved and conversation history is carried to CLI backends;
  tool-call and tool-result events are paired.
- Image tool results are summarized, and the agent's init line is surfaced.

### Changed

- Tree formatted at the configured width with imports sorted.

## 0.2.0 — 2026-09-09

First version after the fork was renamed. **Never tagged** — the version was
bumped in `2d57364` and the tree moved straight on to 0.2.1, so the entries below
describe commits reachable before `v0.2.1` rather than a cut release.

### Added

- Framework-shell `chat` capability as the single builtin capability.
- Agent-loop conversation backends: CLI subprocess and HTTP service families.
- Agent-loop v2 — multiple profiles, local backend detection, and the consult
  mechanism for second opinions.

### Changed

- Default ports moved to 8082 (backend) and 8092 (frontend).
- Agent-loop credential override renamed to `KAG_AGENT_LOOP_API_KEY`.

### Removed

- The in-process agent loop, satellite subsystems and learning layer.
- The RAG / knowledge-base layer, end to end.

### Fixed

- Bare-turn error boundary and turn-pipeline tests.
- zh-CN Windows startup crash; `/api/tools` ImportError.

[Unreleased]: https://github.com/ontoweb-cn/kagweb/compare/v0.2.2...HEAD
[0.2.2]: https://github.com/ontoweb-cn/kagweb/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/ontoweb-cn/kagweb/releases/tag/v0.2.1
