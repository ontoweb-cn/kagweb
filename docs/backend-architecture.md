# KAGWeb 后端架构

> 适用版本：KAGWeb 0.2.2（`kagweb/__version__.py`）· Python 3.11+
> 代码根目录：`kagweb/`；本文所有路径均相对仓库根目录。
> 核对基准：**tag `v0.2.2`**（2026-09-20 实测）。测量在版本号提交 `aad0eb1` 上完成；该提交到 tag 之间**只有文档改动、代码逐字节相同**（`git diff --stat aad0eb1 v0.2.2 -- kagweb/ kagweb_cli/ kag-bridge/ web/ pyproject.toml` 为空），故**正文所有数字与行号对 tag 同样成立**。
> 历史基准：`c3ffe57`（2026-09-10 初版）、`ef8bbf5`（2026-09-11 批次四/五移除后）。修正记录表中标注「初版」的数字即来自 `c3ffe57`。
> **批次四/五移除已全部落地**（IM 通道合伙人、人格、学习者+监护人、内置工具包、MCP 客户端栈），且初版列出的三项待修项——`ARCHITECTURE.md` 的 WS 路径、技能资产打包、幽灵空目录——**均已修复**，见 §9。本版另新增 KAG 管理面（`services/kag/` + `/api/kag`）。
> 关联文档：[`backend-llm-deployment.md`](./backend-llm-deployment.md)（LLM 部署机制与 8 条产品决策）、[`kag-integration-design.md`](./kag-integration-design.md)、[`../ARCHITECTURE.md`](../ARCHITECTURE.md)
>
> **注意**：本文正文描述的是**现状**（代码等于 `aad0eb1`，即 tag `v0.2.2` 的代码）。§2 末的历史偏差小节、§9 中标注为历史记录的条目保留的是 `c3ffe57`/`ef8bbf5` 基线的结论，现状以 [`../ARCHITECTURE.md`](../ARCHITECTURE.md) 为准。`backend-llm-deployment.md` §五的 8 条产品决策中，决策 3/4/6/7 已全部落地。

## 评审修正记录（2026-09-10，逐条对照代码核实）

> **状态说明**：本表是 `c3ffe57` 基线的历史评审记录，**不再逐条更新**——其中多项针对的代码（`tools/`、`partners/`、`persona/`、`skills/`）已随批次四/五整体移除。保留它是为了记录当时的核对方法与结论，当前状态的权威来源是正文与 §9。

本文由一次后端分析整理而来，成文前对原文逐条实测复核，修正如下：

| # | 严重度 | 问题 | 修正 |
|---|---|---|---|
| 1 | 高 | 原文称「文档与代码一致，不是缺陷」——**不成立**。`ARCHITECTURE.md:13` 写 `WebSocket /ws (/api/unified/ws)`，而 `/api/unified/ws` 在全仓库仅此一处出现，代码中并不存在；真实路径只有 `/ws`（`api/routers/unified_ws.py:41`，`main.py:508` 无前缀挂载）。且有测试**显式断言**统一 WS 无前缀（`tests/api/test_websocket_routing.py:18`） | §2 单列「文档与代码的偏差」并引测试为证；§9 记为待修项 |
| 2 | 中 | 原文称上游移除清单包含「记忆、技能」，**过度概括**。`kagweb/skills/builtin/` 下 5 个 `SKILL.md`（docx/pdf/pptx/xlsx/skill-creator）仍被 git 跟踪，且被 `pyproject.toml` 的 package-data 规则 `"**/*.md"` 打进 wheel | §9 更正为「技能运行时已移除，技能资产留在包内」 |
| 3 | 中 | 原文未识别**死引用**：`allowed_builtin_tools` 的文档与多处代码注释仍以 `read_memory` / `web_fetch` / `rag` 为例（`core/context.py:72`、`api/utils/tool_options.py:35`、`services/partners/manager.py:209`），但工具层从未注册过这些名字（`tools/builtin_specs.py:45` 只有 4 个） | §9 新增「已移除层的残留接缝」 |
| 4 | 低 | 端点数量「约 215 个」为估计值 | 实测：router 装饰器 214 个（HTTP 211 + WS 3）+ app 级装饰器 3 个 = **214 HTTP + 3 WS**；§1 改用实测值 |
| 5 | 低 | 后端规模「约 97,500 行」 | 实测 **412 个 Python 文件 / 98,169 行**（不含 `__pycache__`，含 `kagweb/` 顶层 3 个文件） |
| 6 | 低 | `ChatCapability.run()` 引用为 `capability.py:98` | 实为 **112** 行；98 行处是 `manifest` 声明 |
| 7 | 低 | 原文表格「IM 通道（16 个平台）」行把 `partners/` + `services/partners/` 计为 24,914 行，**漏计 `services/partner_groups/`**（2,540 行） | §1 修正为 20,454 行并注明三个目录 |
| 7b | 低 | 本文初稿的模块表同样漏计 `app/`、`logging/`、`events/`、`i18n/`、`plugins/`、`config/` 六个模块 | §1 已补全，现为 15 行且逐项与 `find` 输出对齐 |
| 8 | 补充 | 原文未提**幽灵空目录**：`kagweb/knowledge`、`kagweb/services/rag`、`embedding`、`web_source`、`github_source` 均为 0 文件且未被 git 跟踪 | §9 新增 |

**复核确认成立、无需修正的核心结论**：`ChatOrchestrator` 的单一漏斗结构（全仓库仅在 `runtime/turn_engine.py:23-25` 被实例化）；双侧存储同协议；CLI 后端 `_MAX_CONCURRENT_TURNS = 4`；`MAX_LINE_BYTES = 4 MiB`；内置工具恰好 4 个；测试 221 个 `test_*.py`。

## 目录

1. [定位：不是一个聊天服务](#1-定位不是一个聊天服务)
2. [主干调用链](#2-主干调用链)
3. [Agent Loop 适配层：两类后端族](#3-agent-loop-适配层两类后端族)
4. [轮次与会话存储](#4-轮次与会话存储)
5. [LLM 供应商层](#5-llm-供应商层)
6. [API 与入口点](#6-api-与入口点)
7. [多用户与授权](#7-多用户与授权)
8. [设计评述](#8-设计评述)
9. [已知偏差与技术债](#9-已知偏差与技术债)

---

## 1. 定位：不是一个聊天服务

KAGWeb 后端是**框架外壳（framework shell）**，不是「带 LLM 调用的聊天服务」。它把会话之外的一切——多用户、鉴权、授权、会话存储、流式传输、供应商接入、IM 通道、部署——都做完整了，唯独把「这一轮对话如何产生」委托给一个**外部 agent loop**。

由此推出一个关键前提：`kagweb/capabilities/chat/` 的默认实现**故意是 stub**，每轮只回一句本地化的壳提示。真正的对话发生在外部进程或服务里。

### 规模实测

以下为 `aad0eb1`（v0.2.2）实测，口径与初版一致：`find -name "*.py" -not -path "*/__pycache__/*"`，含 `kagweb/` 顶层 3 个文件。

| 模块 | 文件 | 行数 | 职责 |
|---|---:|---:|---|
| `services/` | 200 | 47,774 | 会话、LLM、配置、解析、agent_loop、KAG 管理、codebuddy_auth 等 |
| `api/` | 24 | 7,409 | HTTP/WS API（含 `/api/kag` 管理面；合伙人/人格/学习者/设备/工具端点已随子系统移除） |
| `runtime/` | 30 | 5,652 | 编排、注册表、协调、leader 选举 |
| `utils/` | 13 | 3,323 | 文档抽取、文件类型等 |
| `multi_user/` | 11 | 1,593 | 鉴权、授权、审计 |
| `capabilities/` | 5 | 1,397 | 轮次能力层（含 `chat` 唯一内置实现） |
| `app/` | 6 | 787 | `ApplicationContainer`、`KAGWebApp` 门面 |
| `core/` | 8 | 648 | 协议定义（context / stream / capability；`tool_protocol.py` 已随 MCP 移除） |
| `logging/` | 10 | 605 | 结构化日志 |
| `events/` | 2 | 220 | 遗留 `EventBus`（不在流式热路径） |
| `plugins/` | 2 | 174 | 插件加载 |
| `config/` | 6 | 151 | 顶层配置常量 |
| `i18n/` | 3 | 110 | 国际化词条 |
| `kagweb/*.py`（顶层） | 3 | 17 | `__init__` / `__main__` / `__version__` |
| **合计** | **323** | **69,860** | 测试另计：**185** 个 `test_*.py` |

**增长来源**：新增 KAG 管理面（`services/kag/` 1,106 行 + `api/routers/kag.py` 818 行）；`services/agent_loop/` 增厚至 6,048 行（ACP 后端、身份桥接、opencode 续接）。**缩减来源**：codex 相关链路整体退役（`services/codex_auth/`、`openai_codex` provider —— `services/llm/` 9,479 → 9,233 行）。

> `partners/`（IM 通道）、`tools/`（内置工具包）、`services/persona/`、`services/mcp/` 均已整目录移除，不再计入。

> `kagweb/knowledge/`、`kagweb/skills/`、`services/{rag,embedding,web_source,github_source}/` 这些**幽灵空目录现已全部不存在**（初版曾列为本表排除项，见 §9 历史记录）。

职责分组（跨目录，均按目录整树求和）：

| 职责 | 位置 | 行数 |
|---|---|---:|
| 对话编排 | `runtime/orchestrator.py`(172) + `runtime/turn_engine.py`(41) | 213（`runtime/` 全树 5,652） |
| Agent loop 适配 | `services/agent_loop/` | 6,048 |
| 会话与轮次存储 | `services/session/` | 9,523 |
| LLM 供应商层 | `services/llm/` | 9,233 |
| 配置与模型目录 | `services/config/` | 6,370 |
| 文档解析 | `services/parsing/` | 4,812 |
| KAG 管理面（新增） | `services/kag/` | 1,106 |

> `services/agent_loop/` 从初版的 3,219 行增至 6,048 行：新增 ACP 后端（`acp_backend.py` + `acp_session_store.py`）、`identity.py`/`identity_store.py`（多用户身份桥接）、opencode 续接支持，并把 `consult.py`、`workdir.py` 纳入同一目录。

## 2. 主干调用链

```
CLI(kagweb_cli)   WebSocket /ws        Python SDK(KAGWebApp)
      │                │                      │
      └────────────────┼──────────────────────┘
                       ▼
        TurnApplicationService  (kagweb/app/service.py:17)
                       ▼
        TurnRequestPreparer     (services/session/turns/request_preparer.py:95)
                       ▼
        TurnExecutor._run_turn  (services/session/turns/executor.py:183)
                       ▼
        TurnEngine.execute      (runtime/turn_engine.py:17)
                       ▼
        ChatOrchestrator.handle (runtime/orchestrator.py:58)
           cap_name = context.active_capability or "chat"
                       ▼
        CapabilityRegistry → ChatCapability.run   (capabilities/chat/capability.py:127)
                       │
          ┌────────────┴────────────┐
   无后端配置 → _run_shell_notice   有配置 → _run_agent_loop
      (capability.py:190)                    (capability.py:210)
                                             ▼
                            AgentLoopBackend  (services/agent_loop/)
                       ▼
        中性 AgentLoopEvent → _AgentLoopRoundBridge → StreamBus → 消费者
```

**两个结构性事实：**

1. **单一漏斗**。`ChatOrchestrator` 在全仓库**仅在** `runtime/turn_engine.py:22-26` 被实例化。CLI、Web、SDK 三条入口全部经由同一个 `TurnApplicationService` → `TurnEngine` → `ChatOrchestrator`，不存在两套对话实现。（批次四移除了第四条入口 Partner/IM 通道。）

2. **延迟导入是有意为之**。`turn_engine.py:18-19` 的注释说明：lazy import 避免进程启动时的 provider/plugin 导入副作用，同时留下唯一的稳定 patch 点供测试与嵌入方使用。

### 文档与代码的偏差（初版项，已修复）

初版在此记录了 `ARCHITECTURE.md:13` 的一个失准：它把 WS 路径写作 `WebSocket /ws (/api/unified/ws)`，而 `/api/unified/ws` 在全仓库不存在。**该项已修复**：`aad0eb1` 的 `ARCHITECTURE.md:13` 已写作 `CLI (kagweb_cli)   WebSocket /ws   Python SDK (KAGWebApp)`，与代码一致。

守护测试仍在，且已随 WS 端点收敛而收窄——批次四移除了 `/ws/partners` 与 `/ws/partner-groups`，现在只剩一个端点：

```python
# tests/api/test_websocket_routing.py:9
def test_websocket_routes_share_one_canonical_namespace() -> None:
    expected_paths = {"/ws"}                      # :12
    prefixes = {id(unified_ws.router): ""}        # ← 显式断言统一 WS 无前缀
```

即：代码是**有意**把所有 WS 端点收在 `/ws` 下的。该测试还断言 WS 路由**不得**携带 `require_signed_in`（HTTP 专用鉴权依赖，`:21`），与 §6 所述「WS 鉴权在 handler 内自理」互为印证。

WS 端点现状：仅剩 `/ws`（统一轮次协议，`api/routers/unified_ws.py:41`，`api/main.py:380` 无前缀挂载）；`/ws/partners` 与 `/ws/partner-groups` 已随批次四移除。

> 附带说明：`AGENTS.md:107` 写的 `kagweb/api/routers/unified_ws.py` 是**模块路径**（确实存在），不涉及 URL，无需修正。

## 3. Agent Loop 适配层：两类后端族

后端不把供应商差异往上传播。所有差异被压缩到 `services/agent_loop/protocol.py` 定义的**中性 schema**：

```python
AgentLoopEvent: kind ∈ {content, thinking, tool_call, tool_result, progress, usage, error}
                text / name / data
AgentLoopRequest: prompt / history / session_id / language / workdir
MAX_LINE_BYTES = 4 MiB
```

能力层和前端永远看不到 vendor 专有形状。

### CLI 族（`cli_backend.py`）

每轮一个子进程。生命周期（提示词入 argv、stdout 流式、stderr 排空、墙钟超时、取消即杀、非零退出 → 失败轮 + stderr 尾部）是共享的；供应商差异只剩「argv 模板 + 纯行翻译器」：

| 预设 | 命令 | 翻译器 |
|---|---|---|
| `claude-code` | `claude -p --output-format stream-json --verbose` | `translate_claude_code` |
| `codex` | `codex exec --json` | `translate_codex` |
| `opencode` | `opencode run --json` | `translate_generic` |
| `custom-cli` | 由设置提供 | `translate_generic` |

三个翻译器都是**纯函数**，因此可脱离进程单独测试。加一个新 CLI 后端 = 一个模板 + 一个翻译器，不动 capability。

**profile 的 `model` 与 `context_window`**（2026-09-10 新增）：

- `model` — 该后端应运行的模型。CLI 族在 `args` 里以 `{model}` 占位符引用（未配置 model 时含该占位符的整条参数被丢弃，于是 CLI 用自己的默认）；HTTP 族作为请求体字段发送（未配置时不发该键）。ACP 族记录备用。
- `context_window` — 该后端的真实窗口，用于历史预算。`0` = 未配置。**注入路径受时序约束**：`executor` 在 `_run_turn` 内建 context，早于 capability 解析 profile，因此值由 `request_preparer` → payload 内部键 `agent_loop_context_window` → `builder.build(context_window_override=…)` 传递。未配置时不注入，预算链保持原样。

**信任边界（设计得最扎实的一处）**：子进程环境是**白名单**的，只有 PATH/HOME/TMPDIR 等基础变量加上运营者显式配置的 `env` 块。服务器环境携带 `AUTH_PASSWORD_HASH`、`POCKETBASE_ADMIN_PASSWORD` 以及启动时导出的供应商密钥，这些**不会**流向子进程。

> 该族**以服务器进程的权限运行子进程**，代码与文档多处声明这是 single-operator 形态；多用户部署应使用 HTTP 族。驱动 CLI/ACP 族因此需要显式的 `agent_loop_cli` 授权（见 §7）。

**并发**：每进程 `_MAX_CONCURRENT_TURNS = 4`（`cli_backend.py:126`）——每个 agent loop 都可能是重量级 Node 运行时，故设信号量限流。

### HTTP 族（`http_backend.py`）

每轮一个流式 POST，是**多用户形态**——因为代码执行发生在运营者的服务内，而非 KAGWeb 进程内。

```
POST {url}{turn_path}
Authorization: Bearer <api_key>
{"session_id": …, "language": …, "prompt": …, "history": [{role, content}, …], "model": …}

→ 200: SSE 或 NDJSON，每行一个对象
  {"kind": "content"|"thinking"|…, "text": …, "name": …, "data": {…}}
```

`model` **仅在 profile 配置了它时才出现**。runs 协议同构（`{"input", "conversation_history", "session_id", "model"}`）。

预设 `intellect` / `intellect-team` / `hermes` / `agentscope` / `custom-http` 说同一套契约。**没有 `kind` 的对象回落到启发式翻译器**，使外部服务可以渐进迁移。

### 咨商协议（`consult.py`）

主 loop 通过在回复**末尾**输出一个围栏块请求第二意见：

````
```consult {"agent": "<id>", "question": "…"}
```
````

KAGWeb 解析尾指令 → 运行指定 profile（其事件以 `progress` 形式在 `consult:<name>` 下流出）→ 把结果追加进历史 → 重跑主 loop。由 `consult_budget`（默认 3）约束。咨商会话 id 为 `<session>::consult::<profile_id>`，使有会话状态的后端能保持连续性。咨商失败降级为 trace 注记，**轮次仍然完成**。

## 4. 轮次与会话存储

`services/session/protocol.py` 定义 `SessionStoreProtocol`，两套实现满足同一契约：

- **SQLite**（`sqlite_store.py`）——单机，WAL 模式，`BEGIN IMMEDIATE` + 偏唯一索引保证「每会话同时只有一个活跃轮次」。
- **PocketBase**（`pocketbase_store.py`）——共享服务器，所有记录按 `user_id` 划范围。

`TurnRecord` 携带：`status`（`queued|running|waiting_input|completed|failed|cancelled`）、`fencing_token`、`state_version`、`failure_code`、`retryable`。

**一致性手段**：`transition_turn` 是对 `expected_status` + `fencing_token` 的 compare-and-set；`append_events` 按 seq 幂等插入。取消或崩溃时持久化部分输出，而非丢弃。

**并行协调**是同构的：`MemoryCoordinator`（单进程）与 `RedisCoordinator`（Lua 脚本实现租约/围栏）满足同一个 `RuntimeCoordinator` 协议。`backend_workers > 1` 强制要求 `redis`（`runtime/coordination/settings.py:54`）。leader 故障时 `TurnRecoveryService`（`coordination/recovery.py:15`）把过期租约的轮次收敛为 `failed` + `worker_lost`，而不是挂死。

> 已知差异：PocketBase 实现的 `parent_message_id` 分支**未接线**（`pocketbase_store.py:583-588` 收下参数后以 `_ = parent_message_id` 丢弃），上下文为线性。SQLite 实现相反，支持完整的编辑分支（`sqlite_store.py:192-195` 的 schema 注释、`:1456` 的「排除任意深度的兄弟分支」上下文构建）。

## 5. LLM 供应商层

`services/llm/` 支持 5 种后端 × 2 种线协议：

- **线协议**：`WireAPI = auto | responses | chat_completions`；`ApiFormat = auto | openai_chat | openai_responses | anthropic`。两者定义在 `services/provider_registry.py:562-569`；`services/llm/config.py:137` 说明区别：`api_format` 是用户可见的协议选择，`wire_api` 是实际发出的线协议。
- **后端**：`openai_compat`（主力，同一 `AsyncOpenAI` SDK 同时说 `/chat/completions` 与 `/responses`，带熔断与回落）、`anthropic`（原生 Messages API，prompt caching ≤4 断点，扩展思考按模型族自适应）、`azure_openai`、`github_copilot`、`codebuddy`。
  > **`openai_codex` 已退役**（2026-09-19，`f112dbc`）：provider 与整条 `services/codex_auth/` 凭据链路一并移除。`multi_user/model_access.py:54` 的注释说明 `openai_codex` 仍留在 `OWNER_BOUND_BINDINGS` 里是**有意的**——旧 catalog 里可能残留此类 profile，保留该绑定意味着它「永不可授予」，而这正是没人能刷新其凭据时应有的答案。
- **流式**：所有具体 provider 都实现了真流式；`factory.stream()` 对外暴露合并后（`DEFAULT_STREAM_COALESCE_CHARS = 64` / 40ms）的块，并把 reasoning 包进 `<think>` 控制令牌（`factory.py:34` 的 `STREAM_CONTROL_TOKENS`）。
- **重试**：`_call_with_retry` 对瞬态错误（429/5xx/超时）退避重试，并带 Stage-2 图像回落（非视觉模型剥离图像）。

`NoModelConfiguredError` **只在完全没有模型时**抛出；配置了但坏掉的模型抛 `LLMConfigError`，让轮次以真实错误失败——这是刻意的设计，避免静默退回 stub。

## 6. API 与入口点

**FastAPI 应用**在 `api/main.py` 组装。中间件：JSON 错误边界、选择性访问日志、CORS。鉴权由共享依赖 `_auth = [Depends(require_signed_in)]` 施加，`AUTH_ENABLED=false` 时是 no-op。

主要路由组（**121 HTTP + 1 WS** 端点，2026-09-20 实测）：

| 前缀 | 端点 | 职责 |
|---|---:|---|
| `/api/settings` | 59 | UI 偏好、模型目录、网络、agent-loop、解析、OAuth、身份关联（含 1 个**免鉴权**的 `public_router`：`GET /api/settings/ui`） |
| `/api/auth` | 14 | 登录/登出/注册、档案、CodeBuddy OAuth 回调（公开） |
| `/api/kag` | 13 + 1 | **KAG 管理面**（项目/模式/成员/图查询/构建/任务），另有 `bridge_router` 的 1 个服务间上报端点（`/api/kag/bridge/tasks`，api_key 鉴权、无 JWT） |
| `/api/system` | 10 | 系统、健康、诊断 |
| `/api/sessions` | 8 | 会话 CRUD、trace 导出、组织、测验结果 |
| `/api/multi-user` | 4 | 授权（模型/执行/Agent 后端；MCP/工具维度已移除） |
| `/api/capabilities` | 3 | 能力清单与可调项（`capabilities.py` 1 个 + `capabilities_settings.py` 2 个） |
| `/api/imports` | 2 | 导入 |
| `/api/voice` | 2 | 语音 |
| `/files/outputs` | 2 | 产物 |
| `/files/attachments` | 1 | 附件 |
| （app 级） | 3 | `/`、`/health/live`、`/health/ready`（`api/main.py:386-398`，不经 router） |
| **`/ws`** | **1** | **统一轮次协议（start_turn / subscribe / cancel / reply / regenerate）** |

> 计数口径：`grep -rhoE '@[a-z_]*router\.(get|post|put|patch|delete|head)\(' kagweb/api/` → **118**（含 `@public_router` 的 1 个与 `@bridge_router` 的 1 个），加 app 级 `@app.*` 的 **3** 个 = **121**；WS 端点经 `unified_ws.py:41` 的 `@router.websocket("/ws")` 声明，`api/main.py:385` 无前缀挂载。
>
> **较初版的 214 HTTP + 3 WS 减少 93 HTTP / 2 WS，但新增 KAG 管理面 14 个**：合伙人 66 个（44 + 22）、工具层 `/api/tools`、人格 `/api/personas`、学习者/监护人约 20 个、MCP 两个路由，加上 `/ws/partners`、`/ws/partner-groups`。（同一口径复算：`c3ffe57` 为 211 + 3 = **214** HTTP / 3 WS；`ef8bbf5` 为 123 + 3 = **126** HTTP / 1 WS。）

**三个入口**：

1. **CLI**（`kagweb_cli/main.py`，Typer）——`run` / `start` / `stop` / `restart` / `serve`，加 `chat`、`session`、`provider`、`plugin`、`config` 五个子命令组（`main.py:37-41`），另有 `init`（`init_cmd.py:387`）与 `doctor`（`doctor.py:43`）在各自模块注册。
2. **Python SDK**（`kagweb/app/facade.py:23` 的 `KAGWebApp`）——`start_turn` / `stream_turn` / `cancel_turn` / `submit_user_reply` / `regenerate_last_turn` 等。
3. **WebSocket**——统一轮次协议，带 `protocol_version` 与命令确认（见 ADR-0003）。

**前端不由后端静态托管**。`kagweb start` 派生两个兄弟进程：uvicorn 与 Node.js Next 服务；`web/proxy.ts` 作为 Next 中间件把 `/api/*`、`/ws/*` 改写转发到后端。

**流式路径**：能力 emit 到该轮的 `StreamBus` → `TurnLifecycle._publish_live_event` 分配 seq、落 `execution.events`、推给订阅者 → `TurnApplicationService.subscribe_turn` 做「持久化积压回放 + 协调器实时事件」 → `unified_ws.py` 逐帧 `safe_send`。

> 注意区分两个 bus：`runtime/stream_bus.py` 的 `StreamBus` 是**每轮**风扇出总线，在聊天流式路径上；`events/event_bus.py` 的 `EventBus` 是遗留单例，只发 `CAPABILITY_COMPLETE` 等生命周期事件，**不在**流式热路径上。

## 7. 多用户与授权

**鉴权是单一接缝**：`require_signed_in`（`api/routers/auth.py:375`，原名 `require_learning_surface`）从 Bearer 头或 `dt_token` cookie 取 JWT，把 `CurrentUser` 装进 ContextVar。工作区路径按作用域解析：管理员 `data/`、普通用户 `data/users/<uid>`（`multi_user/paths.py:7`）。**`data/partners/<id>` 已随合伙人子系统移除**。

> **例外**：`/api/kag/bridge/tasks` 不挂 `_auth`——它是服务间上报端点（KAG 侧调用），用 api_key 鉴权（`api/main.py:373` 的注释与 `kag-integration-design.md` 附录 A.3）。

**授权按资源分派**，各管一段：

| 维度 | 位置 | 策略要点 |
|---|---|---|
| 模型访问 | `model_access.py` | 归属者绑定的凭据（`openai_codex`、`codebuddy`）**永不可授予**（`is_owner_bound`，`:58-68`） |
| Agent 后端（HTTP 族） | `model_access.py:124-131` `agent_loop` | 部署级：管理员配了后端即视为允许用户使用（`is not False`），`False` 可暂停单个用户 |
| Agent 后端（CLI/ACP 族） | `model_access.py:134-137` `agent_loop_cli` | **默认拒绝**（`is True` 才放行）：该族以服务器 uid spawn 本地进程，授权它等同于授予本机代码执行 |
| 执行 | `tool_access.py:21` `exec_override` | 三态覆盖部署级 exec 策略（`None` 跟随部署 / `False` 恒拒） |

> 2026-09-11：MCP 客户端栈整体移除（`services/mcp/`、`space_mcp`/`mcp_settings` 路由、Space MCP 页、`runtime/providers/`、`ToolRegistry`/`ScopedToolRegistry`、`core/tool_protocol.py`、grant 的 `mcp_tools` 维度）。移除前 MCP 工具已无投递通路（`build_tool_view` 零生产调用者），仅配置面活跃。**`aad0eb1` 复测确认：`runtime/providers/`、`services/mcp/`、`tool_protocol`、`ScopedToolRegistry` 在全仓库零残留。**

**轮次门禁**（`services/session/turns/request_preparer.py`）对**每个非管理员回合**无条件执行（`:200`），判定"这一轮需要的资源"是否已授权：`CapabilityManifest.required_service` 给出静态答案，`_effective_required_service()`（`:42`）修正 `chat` 的部署相关情形——配了 agent 后端时它委派给后端、不碰 KAGWeb 自己的 LLM 层，因此按 family 落到 `agent_loop` 或 `agent_loop_cli`；没有后端时（壳 stub）仍是 `llm`。判据经 preset 注册表派生（`profile_family()`），**不再枚举预设名**。

> 该门禁曾经只在"调用方未固定 `llm_selection`"的分支里执行，而 `llm_selection` 是客户端可传的协议字段——固定它即可跳过资源检查。现已移到分支之外，见 §8.3 与 `tests/services/session/test_turn_access_gate.py`（含走真实 `start_turn` 的用例）。

## 8. 设计评述

**做得扎实的地方**

1. **供应商差异被压到最小表面积**。CLI 族 = argv 模板 + 纯行翻译器；HTTP 族 = 一个小契约 + 启发式回落。新增后端不需要改 capability、不需要改前端。
2. **安全边界被显式建模，而非靠约定**。子进程环境白名单（`cli_backend.py:113` `_build_child_env`，ACP 族复用同一函数）、工作目录允许根校验。~~每轮动态组装的工具作用域（`ScopedToolRegistry`）~~ —— **该条已随工具层移除**：现在 KAGWeb 不持有任何工具，agent backend 自带工具集，边界收敛为「谁能驱动后端」（`agent_loop` / `agent_loop_cli` 授权维度）。
3. **双实现同协议 + 围栏令牌**。存储与协调器都能从单机平滑切到多 worker，且崩溃/取消有明确收敛语义（`worker_lost` 而非挂死）。
4. **接入点收敛**。`ARCHITECTURE.md` 标明的三处扩展缝（换委派逻辑、注册能力、加后端预设）确有对应的最小改动面。

**值得商榷的地方**

1. **`ChatOrchestrator` 当前接近直通**。它做 capability 路由 + StreamBus 生命周期 + 完成事件发布，但只有 `chat` 一个能力，路由价值尚未兑现——这是为将来预留的结构，不是当下必需。
2. **`capabilities/_shared.py` 的 `emit_capability_result()` 与实际持久化分工需要读代码才能厘清**：capability 只写 `context.capability_output` 和 stream，**真正落库的是 `TurnExecutor`**。文档没有把这个边界说清楚，容易误读。
3. **纯 agent-loop 部署的门禁已解耦，但按 family 分了档**（2026-09-10 修复）。原先门禁硬编码 `has_capability_access("llm")`，非管理员在多用户部署下每一轮都被拒（`backend-llm-deployment.md` P0-1）；现在按回合实际需要的资源判定。**但 CLI/ACP 族不沿用"默认允许"**：它 spawn 的本地进程以服务器 uid 运行且无沙箱，因此该授权默认拒绝、需管理员显式勾选——这是权限按风险分档，不是恢复阻断。HTTP 族保持默认允许，多用户可用性不受影响。

## 9. 已知偏差与技术债

**状态总览**：初版（`c3ffe57`）列出的 8 项中，**第 1、2、4、5 项已修复**（WS 路径文档、死引用、幽灵目录、`source_manifest` 链路），第 3 项（技能资产）随批次四/五**以「技能运行时 + 资产一并移除」的方式关闭**。以下是 `aad0eb1` 实测的当前清单。

| # | 严重度 | 问题 | 位置 | 建议 |
|---|---|---|---|---|
| 1 | 低 | **`kagweb/config/constants.py` 整体是死模块**：全文件 30 行，`VALID_INVESTIGATE_TOOLS` / `VALID_SOLVE_TOOLS` / `LOG_LEVEL_TAGS` 三个常量**零外部引用**（`config/` 下只有 `settings.py` 被 `services/llm/factory.py:11` 使用）。前两者是已移除的 solve/investigate agent 时代的残留 | `kagweb/config/constants.py` | 删除该文件；`config/` 随之只剩 `settings.py` |
| 2 | 低 | **`allowed_builtin_tools` 字段与其文档已过时**：字段本身仍被 selection-tutor 使用（`executor.py:508` 传 `[]`），但注释描述的语义——「whitelist gating the built-in auto-mounted tools」「partners set this so an owner can deny built-ins per companion」——指向的词表与合伙人机制**都已不存在**（`tools/` 目录已删） | `core/context.py:71-75`（注释）、`:98`（字段）、`executor.py:508` | 字段保留（仍有唯一消费方），改写注释：它现在的含义只是「selection-tutor 侧边栏不挂载任何内置工具」 |
| 3 | 低 | **示例代码引用已移除的能力**：`CapabilityManifest` 的 docstring 示例用 `deep_solve` 能力与 `tools_used=["rag", "web_search", "code_execution"]`，这些都是已删子系统 | `core/capability_protocol.py:60-64` | 换成 `chat` 或中立示例名 |
| 4 | 低 | **`parent_message_id` 在两套存储实现间能力不对等**：SQLite 支持完整编辑分支，PocketBase 收下参数后丢弃（`pocketbase_store.py:588` 的 `_ = parent_message_id`），上下文退化为线性 | `services/session/pocketbase_store.py:583-588` vs `sqlite_store.py:192-195` | 在 PocketBase 实现或文档中显式声明这一降级，避免共享部署下用户看到静默差异 |
| 5 | 低 | **后台命令子协议已成死链**：`BackgroundCommandKind` 只剩 `CRON_RELOAD` 一个取值（`coordination/types.py:37-38`），而 `submit_background_command` 在**生产代码中零调用者**（仅测试调用）；grep `cron` 无任何模块。更关键的是消费侧也短路了——`BackgroundLeaderSupervisor` 的 `control_callback` 在 `api/main.py:132-138` **从未传入**，于是 `background_leader.py:184` 的 `if self.control_callback is None: return` 让 `_drain_background_commands` 永远空转 | `runtime/coordination/{types,protocol,memory,redis}.py`、`runtime/background_leader.py:182-193`、`api/main.py:132-138` | 若不打算恢复 cron，可删除 `BackgroundCommandKind` 与该三方法（`submit`/`read`/`acknowledge_background_command`）及 `control_callback` 钩子；**注意保留 `recovery_callback`**——轮次恢复是多 worker 部署的必需项，与这条死链无关 |

### 已修复项（历史记录）

初版曾列以下 5 项，均在 `c3ffe57` → `aad0eb1` 之间关闭：

| 初版项 | 结论 | 关闭方式 |
|---|---|---|
| `ARCHITECTURE.md:13` 写 `/api/unified/ws`（代码不存在） | ✅ 已修复 | `ARCHITECTURE.md:13` 现为 `WebSocket /ws`；守护测试 `tests/api/test_websocket_routing.py:9` 保留并把 `expected_paths` 收窄为 `{"/ws"}`（`:12`） |
| `allowed_builtin_tools` 注释里的 `rag` / `read_memory` / `web_fetch` 死引用 | ✅ 已修复 | 引用这些名字的伙伴/工具代码整体移除；字段本身降级为 selection-tutor 单一用途（见上表 #2） |
| 5 个 `SKILL.md` 资产与已移除的技能运行时不一致 | ✅ 已关闭 | 随批次四/五**连同资产一并移除**：`kagweb/skills/` 目录不存在，`pyproject.toml` 的 `"**/*.md"` 规则已删（现在 `kagweb_cli = ["**/*.md"]` 是唯一 `.md` 打包规则） |
| 幽灵空目录（`knowledge/`、`services/rag/` 等 6 个） | ✅ 已修复 | `aad0eb1` 实测：6 个目录**全部不存在** |
| `memory_context` / `skills_manifest` 残留字段 | ✅ 已处理 | 字段已删；`source_manifest` 保留为附件到达后端的**唯一通路**（`executor.py:468` 生成 → `capability.py:710` 注入 `Attached sources:` 块） |

> `source_manifest` 链路的完整修复记录（物化门控经 `profile_family()` 修正、会话删除回收 workspace、附件 E2E 验证）保留在 `backend-llm-deployment.md` §四 #6 的决策记录中，此处不再重复。

### 与上游的差异

仓库至今 **200 个提交**（截至版本号提交 `aad0eb1`；tag `v0.2.2` 在其后仅增加文档提交）。其中前 5 个即完成了从 DeepMentor 1.6.4 的裁剪：`74fdb51` 导入基线 → `a233d63` 剥离 agent loop / 卫星 / 学习层 → `cd4b815` 端到端移除 RAG 知识库层 → `af10330` 全局重命名 → `ee28b03` 品牌重写。移除清单（`ARCHITECTURE.md:232`）涵盖 agent loop + capability graph、RAG/知识库、以及记忆/技能/cron/沙箱/subagent/课程/阅读/笔记等卫星子系统。后续批次又移除了合伙人+IM 通道、人格、学习者+监护人、内置工具包与 MCP 客户端栈；最近一轮（2026-09-19）退役了 codex 凭据链路并新增 KAG 管理面。

**修正说明**：上述「技能」应理解为**技能运行时**；技能资产（`SKILL.md`）此后也已删除（见上）。

## 验证

```bash
KAGWEB_HOME=/tmp/kw-smoke python -m pytest tests          # 后端
cd web && npm run typecheck && npm run test:node && npx vitest run
```

本文所有数字可用以下命令复现（括号内为 `aad0eb1` 实测值）：

```bash
# 后端规模（排除缓存）
find kagweb -name "*.py" -not -path "*/__pycache__/*" | wc -l                    # 323
find kagweb -name "*.py" -not -path "*/__pycache__/*" -exec cat {} + | wc -l    # 69860
find tests -name "test_*.py" | wc -l                                            # 185

# 端点（HTTP / WS 拆分）
grep -rhoE '@[a-z_]*router\.(get|post|put|patch|delete|head)\(' kagweb/api/ | wc -l  # 118
grep -rhoE '@app\.(get|post|put|patch|delete)\(' kagweb/api/ | wc -l                  # 3
grep -rhoE '@[a-z_]*router\.websocket\(' kagweb/api/ | wc -l                          # 1

# 已移除子系统应零残留
for d in partners tools services/partners services/persona services/mcp runtime/providers services/rag; do
  echo "$d: $(git ls-files kagweb/$d | wc -l)"; done          # 全部 0

# codex 凭据链路应零残留
git ls-files kagweb/services/codex_auth/ | wc -l              # 0
grep -rl "openai_codex_provider" --include="*.py" kagweb/ | wc -l   # 0

# 死模块（应只有定义行）
grep -rn "VALID_INVESTIGATE_TOOLS\|VALID_SOLVE_TOOLS" --include="*.py" kagweb/
```

> **计数口径警告**：端点表必须区分 `router` 级与 `app` 级——`@app.get("/health/live")` 之类的 3 个端点不经 router，若只 grep router 会漏计；反之若把 `'@[A-Za-z_]+\.(get|post|...)'` 这种不限前缀的模式同时用于 HTTP 与 WS，会把两者混在一个数字里。另外 `@app.middleware("http")` 与 router 装饰器同名不同义，grep 时务必带上 `\(` 锚定到调用形（否则注释与字符串里的 `@router.get` 也会被计入）。
