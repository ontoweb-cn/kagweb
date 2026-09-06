# KAGWeb

KAGWeb is an agent-native intelligent learning companion framework, derived
from [DeepMentor](https://openkg.cn) (OPENKG, Apache-2.0).

This fork strips the product capability layer down to a **framework shell** and
prepares the codebase for a custom KAG backend integration:

- **Kept** — the LLM provider layer (OpenAI Chat Completions + Responses API
  dual-protocol, Anthropic, Azure, Codex, Copilot, ...), the turn orchestrator
  with capability/tool registries, the streaming event bus, SQLite + PocketBase
  session storage, runtime settings, multi-user/auth/grants, Partners (IM
  channels), the Next.js web front end, and CLI/SDK entry points.
- **Removed** — the agent-loop capability graph (chat loop, deep research,
  question generation, mastery/reading/course surfaces), the RAG / knowledge
  base layer, memory, skills, cron, sandbox execution, and their UIs.
- **Next** — the default `chat` capability is a stub that completes every turn
  with a localized shell notice; the KAG backend plugs in at
  `kagweb/runtime/orchestrator.py` -> `kagweb/capabilities/chat/`.

## Install (development)

```bash
python -m venv .venv && .venv/Scripts/python -m pip install -e ".[dev]"   # Windows
# or: python3 -m venv .venv && .venv/bin/python -m pip install -e ".[dev]"
cd web && npm ci
```

## Run

```bash
kagweb start                 # backend + frontend together
kagweb serve --port 8001     # API server only
kagweb run chat "hello"      # single turn through the stub capability
```

## Provider auth

Provider auth (`openai-codex` OAuth login; `github-copilot` validates an existing Copilot auth session; `codebuddy` validates CodeBuddy SDK auth and starts login when needed) is managed through `kagweb provider login <provider>`. For local Codex OAuth bridging in containers, see `CONTAINERIZATION.md#temporary-local-codex-oauth-bridge`.

## Deployment

The web app stays deployable under a subpath
(e.g. `https://your.host/kagweb` behind Kubernetes/Ingress). See
`CONTAINERIZATION.md`, `deploy/k8s/`, and the subpath rules in `AGENTS.md`.

## License

Apache-2.0, inherited from the upstream project; see `LICENSE` and
`THIRD_PARTY_NOTICES.md`.
