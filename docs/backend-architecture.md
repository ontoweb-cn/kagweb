# KAGWeb 后端架构

> 适用版本：KAGWeb 0.2.1（`kagweb/__version__.py`）· Python 3.11+
> 代码根目录：`kagweb/`；本文所有路径均相对仓库根目录。
> 核对基准：`main` 分支 `c3ffe57`（2026-09-10 实测）
> 关联文档：[`backend-llm-deployment.md`](./backend-llm-deployment.md)（LLM 部署机制与 8 条产品决策）、[`../ARCHITECTURE.md`](../ARCHITECTURE.md)
>
> **注意**：本文描述的是**现状**。`backend-llm-deployment.md` §五已就 Skills、人格、合伙人、LLM 设置、Intellect 对接等做出 8 条移除/改造决策，落地后本文若干章节（尤其 §1 的模块规模表、§7 的多用户授权）将随之变化。

## 评审修正记录（2026-09-10，逐条对照代码核实）

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

| 模块 | 文件 | 行数 | 职责 |
|---|---:|---:|---|
| `services/` | 228 | 56,270 | 会话、LLM、配置、解析、codex_auth 等 |
| `partners/` | 31 | 12,749 | IM 通道实现（16 平台 / 17 模块） |
| `api/` | 31 | 10,775 | HTTP/WS API，214 HTTP + 3 WS 端点 |
| `runtime/` | 39 | 6,680 | 编排、注册表、协调、leader 选举 |
| `utils/` | 12 | 3,256 | 文档抽取、文件类型等 |
| `multi_user/` | 15 | 2,718 | 鉴权、授权、监护人、学习者档案 |
| `tools/` | 10 | 1,800 | 内置工具（4 个） |
| `core/` | 9 | 946 | 协议定义（context / stream / tool / capability） |
| `capabilities/` | 5 | 851 | 轮次能力层（含 `chat` 唯一内置实现） |
| `app/` | 6 | 782 | `ApplicationContainer`、`KAGWebApp` 门面 |
| `logging/` | 10 | 605 | 结构化日志 |
| `events/` | 2 | 220 | 遗留 `EventBus`（不在流式热路径） |
| `i18n/` | 3 | 175 | 国际化词条 |
| `plugins/` | 2 | 174 | 插件加载 |
| `config/` | 6 | 151 | 顶层配置常量 |
| `kagweb/*.py`（顶层） | 3 | 17 | `__init__` / `__main__` / `__version__` |
| **合计** | **412** | **98,169** | 测试另计：221 个 `test_*.py` |

> `kagweb/knowledge/` 与 `kagweb/skills/` 为 0 个 `.py` 文件的幽灵目录，见 §9 第 4 项，未计入本表。

职责分组（跨目录）：

| 职责 | 位置 | 行数 |
|---|---|---:|
| 对话编排 | `runtime/orchestrator.py`、`runtime/turn_engine.py` | 见 `runtime/` 总 6,680 |
| Agent loop 适配 | `services/agent_loop/` | 1,765 |
| 会话与轮次存储 | `services/session/` | 10,052 |
| LLM 供应商层 | `services/llm/` | 9,476 |
| 配置与模型目录 | `services/config/` | 6,032 |
| 文档解析 | `services/parsing/` | 4,812 |
| IM 通道 | `partners/` + `services/partners/` + `services/partner_groups/` | 20,454 |

## 2. 主干调用链

```
CLI(kagweb_cli)   WebSocket /ws        Python SDK(KAGWebApp)
      │                │                      │
      └────────────────┼──────────────────────┘
                       ▼
        TurnApplicationService  (kagweb/app/service.py)
                       ▼
        TurnRequestPreparer     (services/session/turns/request_preparer.py:50)
                       ▼
        TurnExecutor._run_turn  (services/session/turns/executor.py:116)
                       ▼
        TurnEngine.execute      (runtime/turn_engine.py:18)
                       ▼
        ChatOrchestrator.handle (runtime/orchestrator.py:60)
           cap_name = context.active_capability or "chat"
                       ▼
        CapabilityRegistry → ChatCapability.run   (capabilities/chat/capability.py:112)
                       │
          ┌────────────┴────────────┐
   无后端配置 → _run_shell_notice   有配置 → _run_agent_loop
                                             ▼
                            AgentLoopBackend  (services/agent_loop/)
                       ▼
        中性 AgentLoopEvent → _AgentLoopRoundBridge → StreamBus → 消费者
```

**两个结构性事实：**

1. **单一漏斗**。`ChatOrchestrator` 在全仓库**仅在** `runtime/turn_engine.py:23-25` 被实例化。CLI、Web、SDK、Partner 四条入口全部经由同一个 `TurnApplicationService` → `TurnEngine` → `ChatOrchestrator`，不存在两套对话实现。Partner 是复用而非旁路——`services/partners/runtime.py:468` 调用的就是同一个 `get_turn_engine().execute(context)`，只是外层套了合成用户作用域。

2. **延迟导入是有意为之**。`turn_engine.py:20-22` 的注释说明：lazy import 避免进程启动时的 provider/plugin 导入副作用，同时留下唯一的稳定 patch 点供测试与嵌入方使用。

### 文档与代码的偏差

上图的 WS 路径以代码为准。根级架构文档在此处失准：

| 出处 | 声称 | 实际 |
|---|---|---|
| `ARCHITECTURE.md:13` | `WebSocket /ws (/api/unified/ws)` | **`/ws`** ——`/api/unified/ws` 在全仓库仅此一处出现，代码中不存在（`api/routers/unified_ws.py:41` 声明 `/ws`，`api/main.py:508` 无前缀挂载） |

这不是「文档漏更新」那么简单：仓库有一个测试**专门钉死** WS 路由的规范化命名空间。

```python
# tests/api/test_websocket_routing.py:9
def test_websocket_routes_share_one_canonical_namespace() -> None:
    expected_paths = {"/ws", "/ws/partners/{partner_id}", "/ws/partner-groups/{group_id}"}
    prefixes = {id(unified_ws.router): "", ...}   # ← 显式断言统一 WS 无前缀
```

即：代码是**有意**把所有 WS 端点收在 `/ws` 下的（三个端点均属一个 canonical 命名空间）。`ARCHITECTURE.md` 的 `/api/unified/ws` 因此是明确的文档缺陷，而非设计歧义。该测试还断言 WS 路由**不得**携带 `require_learning_surface`（HTTP 专用鉴权依赖），与 §6 所述「WS 鉴权在 handler 内自理」互为印证。

三个 WS 端点的实际路径：`/ws`（统一轮次协议）、`/ws/partners`（`main.py:493`）、`/ws/partner-groups`（`main.py:494`）。

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

**信任边界（设计得最扎实的一处）**：子进程环境是**白名单**的，只有 PATH/HOME/TMPDIR 等基础变量加上运营者显式配置的 `env` 块。服务器环境携带 `AUTH_PASSWORD_HASH`、`POCKETBASE_ADMIN_PASSWORD` 以及启动时导出的供应商密钥，这些**不会**流向子进程。

**并发**：每进程 `_MAX_CONCURRENT_TURNS = 4`（`cli_backend.py:126`）——每个 agent loop 都可能是重量级 Node 运行时，故设信号量限流。

### HTTP 族（`http_backend.py`）

每轮一个流式 POST，是**多用户形态**——因为代码执行发生在运营者的服务内，而非 KAGWeb 进程内。

```
POST {url}{turn_path}
Authorization: Bearer <api_key>
{"session_id": …, "language": …, "prompt": …, "history": [{role, content}, …]}

→ 200: SSE 或 NDJSON，每行一个对象
  {"kind": "content"|"thinking"|…, "text": …, "name": …, "data": {…}}
```

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

> 已知差异：PocketBase 实现的 `parent_message_id` 分支**未接线**，上下文为线性。

## 5. LLM 供应商层

`services/llm/` 支持 6 种后端 × 2 种线协议：

- **线协议**：`WireAPI = auto | responses | chat_completions`；`ApiFormat = auto | openai_chat | openai_responses | anthropic`。
- **后端**：`openai_compat`（主力，同一 `AsyncOpenAI` SDK 同时说 `/chat/completions` 与 `/responses`，带熔断与回落）、`anthropic`（原生 Messages API，prompt caching ≤4 断点，扩展思考按模型族自适应）、`azure_openai`、`openai_codex`（OAuth + 原始 httpx SSE）、`github_copilot`、`codebuddy`。
- **流式**：所有具体 provider 都实现了真流式；`factory.stream()` 对外暴露合并后（64 字符 / 40ms）的块，并把 reasoning 包进 `<think>` 控制令牌。
- **重试**：`_call_with_retry` 对瞬态错误（429/5xx/超时）退避重试，并带 Stage-2 图像回落（非视觉模型剥离图像）。

`NoModelConfiguredError` **只在完全没有模型时**抛出；配置了但坏掉的模型抛 `LLMConfigError`，让轮次以真实错误失败——这是刻意的设计，避免静默退回 stub。

## 6. API 与入口点

**FastAPI 应用**在 `api/main.py` 组装。中间件：JSON 错误边界、选择性访问日志、CORS。鉴权由共享依赖 `_auth = [Depends(require_learning_surface)]` 施加，`AUTH_ENABLED=false` 时是 no-op。

主要路由组（214 HTTP + 3 WS 端点）：

| 前缀 | 职责 |
|---|---|
| `/api/auth` | 登录/登出/注册、档案、设备、Codex OAuth 回调（公开） |
| `/api/sessions` | 会话 CRUD、trace 导出、组织、测验结果 |
| `/api/settings` | UI 偏好、模型目录、网络、agent-loop、解析、OAuth |
| `/api/multi-user` | 授权、监护人、学习者 |
| `/api/capabilities` | 能力清单与可调项 |
| `/api/partners`、`/api/partner-groups` | IM 伙伴与群组 |
| `/api/tools`、`/api/personas`、`/api/system`、`/api/voice` | 工具、人格、系统、语音 |
| `/files/outputs`、`/files/attachments` | 产物与附件 |
| **`/ws`** | **统一轮次协议（start_turn / subscribe / cancel / reply / regenerate）** |
| `/ws/partners`、`/ws/partner-groups` | 伙伴与其群组的 socket |

**三个入口**：

1. **CLI**（`kagweb_cli/main.py`，Typer）——`run` / `start` / `stop` / `serve` / `init` / `doctor`，加 `chat`、`session`、`provider`、`partner`、`plugin`、`config` 六个子命令组。
2. **Python SDK**（`kagweb/app/facade.py:23` 的 `KAGWebApp`）——`start_turn` / `stream_turn` / `cancel_turn` / `submit_user_reply` / `regenerate_last_turn` 等。
3. **WebSocket**——统一轮次协议，带 `protocol_version` 与命令确认（见 ADR-0003）。

**前端不由后端静态托管**。`kagweb start` 派生两个兄弟进程：uvicorn 与 Node.js Next 服务；`web/proxy.ts` 作为 Next 中间件把 `/api/*`、`/ws/*` 改写转发到后端。

**流式路径**：能力 emit 到该轮的 `StreamBus` → `TurnLifecycle._publish_live_event` 分配 seq、落 `execution.events`、推给订阅者 → `TurnApplicationService.subscribe_turn` 做「持久化积压回放 + 协调器实时事件」 → `unified_ws.py` 逐帧 `safe_send`。

> 注意区分两个 bus：`runtime/stream_bus.py` 的 `StreamBus` 是**每轮**风扇出总线，在聊天流式路径上；`events/event_bus.py` 的 `EventBus` 是遗留单例，只发 `CAPABILITY_COMPLETE` 等生命周期事件，**不在**流式热路径上。

## 7. 多用户与授权

**鉴权是单一接缝**：`require_auth`（`api/routers/auth.py:288`）从 Bearer 头或 `dt_token` cookie 取 JWT，把 `CurrentUser` 装进 ContextVar。工作区路径按作用域解析：管理员 `data/`、普通用户 `data/users/<uid>`、伙伴 `data/partners/<id>`。

**授权按资源分派**，各管一段：

| 维度 | 位置 | 策略要点 |
|---|---|---|
| 模型访问 | `model_access.py` | 归属者绑定的凭据（`openai_codex`、`codebuddy`）**永不可授予** |
| 工具/执行 | `tool_access.py` | MCP 工具对非管理员**默认拒绝** |
| 伙伴 | `partner_access.py` | 可管理 = 所有者或管理员 |
| 监护人 | `guardians.py` | 每次调用重新校验身份 |
| 学习者 | `learner_profile.py` | 年龄/年级/课程/阅读水平 |

## 8. 设计评述

**做得扎实的地方**

1. **供应商差异被压到最小表面积**。CLI 族 = argv 模板 + 纯行翻译器；HTTP 族 = 一个小契约 + 启发式回落。新增后端不需要改 capability、不需要改前端。
2. **安全边界被显式建模，而非靠约定**。子进程环境白名单、每轮动态组装的工具作用域（`ScopedToolRegistry` 让归属者级工具根本不进进程级注册表）、工作目录允许根校验。
3. **双实现同协议 + 围栏令牌**。存储与协调器都能从单机平滑切到多 worker，且崩溃/取消有明确收敛语义（`worker_lost` 而非挂死）。
4. **接入点收敛**。`ARCHITECTURE.md` 标明的三处扩展缝（换委派逻辑、注册能力、加后端预设）确有对应的最小改动面。

**值得商榷的地方**

1. **`ChatOrchestrator` 当前接近直通**。它做 capability 路由 + StreamBus 生命周期 + 完成事件发布，但只有 `chat` 一个能力，路由价值尚未兑现——这是为将来预留的结构，不是当下必需。
2. **`capabilities/_shared.py` 的 `emit_capability_result()` 与实际持久化分工需要读代码才能厘清**：capability 只写 `context.capability_output` 和 stream，**真正落库的是 `TurnExecutor`**。文档没有把这个边界说清楚，容易误读。
3. **纯 agent-loop 部署目前只服务管理员**。非管理员仍受 LLM 授权门限制（`request_preparer`）——**本文初稿将其定性为「已知边界，不是 bug」，该定性已被 [`backend-llm-deployment.md`](./backend-llm-deployment.md) 推翻**：在多用户场景下它构成功能性阻断（该文 P0-1），并已列为待修复项。

## 9. 已知偏差与技术债

按严重度排列，均为 2026-09-10 实测：

| # | 严重度 | 问题 | 位置 | 建议 |
|---|---|---|---|---|
| 1 | 中 | **文档与代码不符**：`ARCHITECTURE.md` 声明 WebSocket 路径为 `/api/unified/ws`，该路径在全仓库不存在；`tests/api/test_websocket_routing.py:9` 反而断言 WS 端点共享 `/ws` 命名空间 | `ARCHITECTURE.md:13`；实为 `api/routers/unified_ws.py:41` 的 `/ws` | 改 `ARCHITECTURE.md:13` 为 `WebSocket /ws`。本文核对的断言中**仅此一处**失准；未核对项不在保证范围内 |
| 2 | 中 | **死引用**：`allowed_builtin_tools` 的文档与注释以 `rag` / `read_memory` / `web_fetch` 举例，但工具层从未注册这些名字（`tools/builtin_specs.py:45` 仅 4 个） | `core/context.py:72`、`api/utils/tool_options.py:35`、`services/partners/manager.py:209`、`api/routers/partners.py:692` | 更新示例名或删注释；确认 `exclude_builtin={"read_memory","write_memory"}` 是无害的空操作 |
| 3 | 中 | **技能资产与技能运行时不一致**：运行时已移除，但 5 个 `SKILL.md` 仍在 git 中并被 package-data 打进 wheel（`pyproject.toml:259` 的 `"**/*.md"`）。**注**：该文件 253-254 行的注释显式点名「builtin SKILL.md files」，属有意打包而非疏漏——问题在于运行时已不存在，这些资产是否还有消费方 | `kagweb/skills/builtin/{docx,pdf,pptx,xlsx,skill-creator}/` | 明确取舍：要么确认还有消费方（如作为能力作者素材），要么连同 package-data 注释一并清理 |
| 4 | 低 | **幽灵空目录**：0 文件且未被 git 跟踪，只因 `.gitignore` 未覆盖而残留于工作树 | `kagweb/knowledge/`、`services/rag/`、`services/embedding/`、`services/web_source/`、`services/github_source/` | 本地清理即可（不影响仓库，但会误导 `find` 类统计，本次核算已排除） |
| 5 | 低 | **残留字段**：`UnifiedContext` 仍携带 `memory_context`、`skills_manifest`、`source_manifest`，对应子系统已移除 | `core/context.py:112-116` | 保留可接受（协议兼容），但应在文档标注为「预留/未用」 |

### 与上游的差异

仓库 50 个提交中，前 5 个即完成了从 DeepMentor 1.6.4 的裁剪：导入基线 → 剥离 agent loop / 卫星 / 学习层 → 端到端移除 RAG 知识库层 → 全局重命名 → 品牌重写。移除清单（`ARCHITECTURE.md:232`）涵盖 agent loop + capability graph、RAG/知识库、以及记忆/技能/cron/沙箱/subagent/课程/阅读/笔记等卫星子系统。

**修正说明**：上述「技能」应理解为**技能运行时**。技能资产（`SKILL.md`）仍在包内，见上表第 3 项。

## 验证

```bash
KAGWEB_HOME=/tmp/kw-smoke python -m pytest tests          # 后端
cd web && npm run typecheck && npm run test:node && npx vitest run
```

本文所有数字可用以下命令复现：

```bash
# 后端规模（排除幽灵目录与缓存）
find kagweb -name "*.py" -not -path "*/__pycache__/*" | wc -l          # 412
find kagweb -name "*.py" -not -path "*/__pycache__/*" -exec cat {} + | wc -l   # 98169

# 端点（HTTP / WS 拆分）
grep -rhoE '@[a-z_]*router\.(get|post|put|patch|delete|head)' kagweb/api/ | wc -l   # 211
grep -rhoE '@[a-z_]*router\.websocket' kagweb/api/ | wc -l                          # 3

# 幽灵目录（应为 0 且未被跟踪）
for d in knowledge services/rag services/embedding services/web_source services/github_source; do
  echo "$d: $(git ls-files kagweb/$d | wc -l)"; done
```
