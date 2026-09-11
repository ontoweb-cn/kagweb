# KAGWeb 后端 LLM 部署机制分析与改进建议

> 适用版本：KAGWeb 0.2.1（`kagweb/__version__.py`）
> 核对基准：`main` 分支 `c3ffe57`（2026-09-10 实测）
> 关联文档：[`backend-architecture.md`](./backend-architecture.md)、[`../ARCHITECTURE.md`](../ARCHITECTURE.md)

> **文档结构**：§一~§四为**现状分析**（基于代码实测）；**§五为产品决策安排**（8 条方向性决策及其落地分析）；§六为优先级建议。§五的决策 6/7/8（人格与合伙人移除、Intellect 对接校正）会**反转**本文若干早期建议，反转处已在原位置标注。
> **基准说明**：§一~§四成文于 `c3ffe57`；§五决策 6-8 与「基准漂移复核」小节则在远端领先 20+ 提交后重新实测，Intellect 部分另经跨仓库核对（见决策 8）。
> **落地状态（2026-09-11，`ef8bbf5`）**：批次四/五已实施——合伙人+IM 通道、人格（决策 6，含决策 7 的 SOUL 模板与 `locked_persona` 耦合）、学习者+监护人子系统（决策 4）、内置工具包（决策 3a）均已移除，导航「Learning Agent/Learning Space/Learning」更名为 Chat/Space/Workspace。§五/§六中相应的「待决策」「批次五方向性」条目自此**已落地**；P2 状态见优先级表。

## 结论摘要

KAGWeb 剥离 agent loop 之后，**「LLM 部署」在代码里仍然指两件不同的事**，而两者的边界从未被重新划定：

| 概念 | 归谁管 | 现状 |
|---|---|---|
| **KAGWeb 自己的 LLM 层** | `services/llm/`（4,839 + 4,637 行） | 仅服务 9 个**旁路**调用点；**不在主对话路径上** |
| **Agent backend 的模型** | 外部 CLI / HTTP 服务 | KAGWeb **既不知道也不配置**——schema 里连 `model` 字段都没有 |

主对话路径（`ChatCapability`）**对 LLM 层零依赖**，已确认。但外围仍有四道耦合没有解开，其中两道是**产品级阻断**：

1. **非管理员用户无法使用任何 agent backend**——轮次门禁强制要求「已授权的 LLM 模型」，而 agent loop 部署里根本没有模型可授（P0-1）。
2. **KAGWeb 的工具层整体不可达**——4 个内置工具在 agent loop 模式下永远不会被调用，但仍在 UI 上可开关、在授权里可授予（P0-2）。
3. **上下文窗口错配**——历史裁剪预算按 16K 模型推导，实际消费者是 200K~1M 窗口的外部后端（P1-1）。
4. **模型选择器指向错误对象**——用户在界面选的模型对 agent backend 完全无效，且无任何提示（P1-2）。

**另有两条更紧急的发现**（成文后经跨仓库核对得出，详见 §五决策 8）：

5. **`intellect-team` 预设指向不存在的端点**——该仓库全仓库不存在 `/agent/turn`，而预设落到默认值即指向它，部署必然 404。**当前必然失败**。
6. **Runs 翻译器在权威实现（Rust）下四项全失效**——文本、推理、工具、审批全部读错字段或事件名，且**静默丢弃无日志**。不改则 Intellect 对接不成立。

> 按优先级，**第 5、6 条应最先处理**（§六「批次零」）：它们决定「Intellect 能不能接上」，而第 1-4 条决定「接上之后好不好用」。

---

## 一、LLM 部署的完整流程

### 1.1 两套并存的「模型配置」

```
用户在 Settings → Catalog 配置模型
        │
        ▼
model_catalog.json  ──► resolve_llm_runtime_config() ──► LLMConfig
        │                        │
        │                        ├──► ContextBuilder（历史预算）  ← 主路径耦合点
        │                        └──► 9 个旁路调用点（标题/洞察/工具/诊断）
        │
        └──► 轮次门禁（has_capability_access("llm")）  ← 主路径阻断点
                                                              ⚠ 与 agent backend 无关

Agent backend 的模型
        │
        ▼
完全没有配置面：CLI 靠 ~/.claude、~/.codex 或 args 传 --model；
HTTP 靠服务端自己的配置
```

**关键事实**：`ChatCapability`（`capabilities/chat/capability.py:112-122`）只读 `agent_loop` 设置块并调用 `build_agent_loop_backend()`，**从不触碰 `get_llm_config()`**。`AgentLoopRequest`（`agent_loop/protocol.py:59-75`）的字段只有 `prompt / history / session_id / language / workdir`——**没有 `model`，也没有 `tools`**。

### 1.2 轮次门禁（资源授权）

`request_preparer.py`。门禁在 `start_turn` 中**同步**执行（跑完才 `create_task(_run_turn)`），且对**每个非管理员回合无条件生效**——包括调用方固定了 `llm_selection` 的情形。

```python
# request_preparer.py（节选，2026-09-10 修复后的结构）
current_user = get_current_user()
if not current_user.is_admin:
    # 这一轮实际需要什么资源：chat 在配了 agent 后端时不需要 LLM
    required = _effective_required_service(capability, agent_loop_block=...)
    if not has_capability_access(required):
        raise RuntimeError(...)

if llm_selection:  # 调用方固定了选择：只校验该模型是否被授权
    llm_selection = apply_allowed_llm_selection(llm_selection) or {}
elif not current_user.is_admin:  # 否则固定第一个已授权的可用模型
    ...
```

`required` 的取值：

| 情形 | `required` | 默认 |
|---|---|---|
| 无 agent 后端（壳 stub） | `llm` | 拒绝（需 LLM 授权） |
| HTTP 族后端 | `agent_loop` | **允许**（管理员配了后端即视为允许用户使用） |
| CLI/ACP 族后端 | `agent_loop_cli` | **拒绝**（该族 spawn 服务器权限进程，需显式授权） |

- **管理员**：完全跳过门禁；`llm_selection` 留空 → `executor.py` 捕获 `NoModelConfiguredError` → `llm_config = None` → 轮次继续 → agent backend 正常运行。✅
- **非管理员**：按上表判定；HTTP 族部署可用，CLI/ACP 族需管理员勾选「Run agent processes on this host」。⚠️

> **历史**：本节曾描述门禁硬编码 `has_capability_access("llm")` 且位于 `else` 分支内，导致非管理员在纯 agent-loop 部署下每轮被拒（§三 P0-1），且固定 `llm_selection` 可跳过检查。两者均已修复。


### 1.3 模型解析链

`resolve_llm_runtime_config()`（`provider_runtime.py:597-687`）八步：合并个人 profile → 应用 `LLMSelection` → 取活跃 profile/model → 读字段 → 建 provider 池 → **匹配 provider**（显式 binding → 网关探测 → 模型名匹配 → 本地主机 → 已配置池 → 兜底 openai）→ 组装凭据 → 返回 `ResolvedLLMConfig`。

**注意**：该函数在无模型时**不抛错**，返回 `model=""`；错误由上一层 `get_llm_config()` 抛出（`llm/config.py:196-203`）。

错误语义分层（`llm/exceptions.py:32-47`）：

| 异常 | 含义 | 在 executor 中的后果 |
|---|---|---|
| `NoModelConfiguredError` | **完全没有**模型 | **被捕获**，轮次继续（`llm_config=None`） |
| `LLMConfigError`（其余） | 模型存在但坏掉 | **不被捕获**，轮次以真实错误失败 |

这个分层是刻意设计的，且**当前行为正确**——不要动它。

### 1.4 凭据与子进程环境

CLI 后端的子进程环境是**白名单**的（`cli_backend.py:80-119`），只放行 PATH/HOME/TMPDIR/APPDATA 等进程基础变量，加上运营者 profile 里的 `env` 块。服务器密钥（`AUTH_PASSWORD_HASH`、供应商密钥等）**不会**流向子进程。

白名单刻意包含 `HOME` / `USERPROFILE` / `APPDATA` / `LOCALAPPDATA`——**正是为了让 agent CLI 能找到自己的登录态**（`~/.claude`、`~/.codex`、CodeBuddy 的 AppData）。这是「agent 自带认证」这一假设在代码里的直接体现。

### 1.5 凭据双份：`codex_auth` 与 `codex` CLI 互不相通

| 维度 | KAGWeb 的 `openai_codex` provider | `codex` CLI agent-loop 预设 |
|---|---|---|
| 认证来源 | KAGWeb 自己的 OAuth 存储 `data/system/user-secrets/<owner>/private/openai-codex/` | Codex CLI 自己的 `~/.codex/auth.json` |
| 模型来源 | 登录后发布到模型目录的托管 profile | CLI 配置 / `args` / `env` |
| 消费 `codex_auth`？ | 是 —— **唯一**消费者 | **否** |

`services/agent_loop/` **不 import `codex_auth`**。有测试钉死这一独立性（`tests/cli/test_provider_cli.py:76-80` 断言 `"~/.codex" not in PROVIDER_CMD`）。

**运营后果**：一份 ChatGPT/Codex 订阅不会在两者间共享。登录 KAGWeb 的 provider **不会**让 `codex` agent-loop 预设获得认证，反之亦然。

### 1.6 检测与测试：只看「在不在」，不看「能不能用」

`detect.py` 与 `/agent-loop/test` 端点（`api/routers/settings.py:1184-1246`）都只做存在性检查：

- CLI：`shutil.which(command)`——**不执行子进程，不查版本，不验认证**。
- HTTP：2.5s 超时 GET——**任何 HTTP 响应（含 404）都算「可达」**。

**一个装了 `codex` 二进制但 `~/.codex/auth.json` 无效的部署，检测会报「available」。** 设置页的 Test 按钮同样不做认证检查。

### 1.7 环境变量契约

`.env.example` 只含宿主机端口与 TZ，**不含任何模型或后端凭据**。agent-loop 的环境变量覆盖是另一套（`runtime_settings.py:842-879`）：`KAGWEB_AGENT_LOOP_BACKEND` / `_COMMAND` / `_URL` 与 `KAG_AGENT_LOOP_API_KEY`——**同样没有 model 键**。

---

## 二、真实 LLM 调用点清单

全部经由 `services/llm/factory.py` 的 `complete()` / `stream()`。共 **9 处**，**无一在主对话答案路径上**。

| # | 位置 | 用途 | 路径性质 | 无 LLM 时的降级 |
|---|---|---|---|---|
| 1 | `session/turns/title_service.py:124` | 会话标题 | 轮次后后台 | 截断首条用户消息 |
| 2 | `session/turns/title_service.py:245` | 轮次洞察徽章 | 轮次后后台 | 静默跳过 |
| 3 | `session/context_builder.py:391` | **历史滚动摘要** | **轮次前，主路径** | 退化为截断（长会话丢连续性） |
| 4 | `tools/brainstorm.py:89` | 头脑风暴 | 工具 | **不可达**（见 P0-2） |
| 5 | `tools/reason.py:104` | 深度推理 | 工具 | **不可达**（见 P0-2） |
| 6 | `services/search/consolidation.py:302` | 搜索结果合成 | 工具（**可选**） | 默认走模板，**不调用** |
| 7 | `api/routers/system.py:459` | 设置页「测试连接」 | 设置 UI | 报错 |
| 8 | `services/config/test_runner.py:219` | 诊断自检 | 诊断 | 报错 |
| 9 | `services/config/settings_spec.py:516` | 保存前探测 | 设置 UI | 报错 |

> #6 默认不触发：`web_search()` 仅在显式传入 `consolidation_llm_model` 时才走 LLM 合成（`search/__init__.py:240`），默认路径用 Jinja 模板。

**确认无 LLM 调用的目录**：`services/parsing/`、`services/voice/`、`services/imagegen/`、`services/agent_loop/`、`multi_user/`。

---

## 三、核心问题

### P0-1 非管理员用户无法使用 agent backend（产品级阻断）——✅ 已修复

**现象**：纯 agent loop 部署下，管理员能聊天，**任何非管理员用户的每一轮都被拒绝**，错误为 `"No LLM model is assigned to your account."`

**根因**：门禁把「有可用的 LLM 授权」等同于「允许聊天」，且在 agent backend 被选择**之前**执行。chat 能力实际不需要 LLM 模型，但轮次根本走不到那里。

**证据**：`request_preparer.py:105-134`（门禁）vs `capabilities/chat/capability.py:112-122`（后端选择，无 LLM 依赖）。

**影响**：这与 `ARCHITECTURE.md` 已记录的「纯 agent-loop 部署目前只服务管理员」是同一个问题——但文档把它描述为「待产品决策的已知边界」，而实际上它是**多用户能力被完全阻断**。如果 KAGWeb 的定位是 KAG 后端的 Web 门面，这个阻断必须优先解决。

**修复（2026-09-10）**：门禁改为按回合**实际需要的资源**判定，不再硬编码 `llm`。`CapabilityManifest.required_service` 声明静态需求，`_effective_required_service()` 修正 `chat` 的部署相关情形。

修复过程中发现并处理了两个后续问题，都值得记下来：

1. **权限必须按风险分档，不能只按「是否需要模型」**。CLI/ACP 族以**服务器 uid** spawn 本地子进程且无沙箱（`cli_backend.py` / `acp_backend.py`），用户提示词就是那个进程执行的内容——驱动它等同于本机代码执行。而 `intellect` 预设是 `family="cli"` 且是 auto-primary 的首选，所以默认安装就落在高危族。因此该族**不沿用默认允许**：新增 `agent_loop_cli` 授权维度，`True` 允许、`None`/`False` 拒绝（与 HTTP 族的 `agent_loop` 默认方向**相反**）。HTTP 族保持默认允许，多用户可用性不受影响。
2. **门禁曾可被绕过**：第一版修复把检查放在「调用方未固定 `llm_selection`」的分支里，而 `llm_selection` 是客户端可传的协议字段——固定它即可跳过资源检查。现已移到分支之外无条件执行，并补了走真实 `start_turn` 的测试（`tests/services/session/test_turn_access_gate.py`）。**纯函数测试看不出这类问题**：分支结构才是被测对象。

> **残留**：CLI/ACP 子进程仍以服务器 uid 运行、无沙箱，这是架构既定（文档多处声明 single-operator shape）。本轮只是把「谁可以启动它」收敛为管理员显式授权，**不等于隔离**。

### P0-2 KAGWeb 工具层整体不可达

**现象**：4 个内置工具（`brainstorm` / `web_search` / `paper_search` / `reason`）在 agent loop 部署下**永远不会被调用**，但：

- 在 `/api/tools` 中列为可用；
- 在设置页可开关（`enabled_optional_tools`）；
- 在授权体系里可授予（`grants.enabled_tools`、`multi_user/tool_access.py`）。

**根因**：工具的设计假设是「LLM 用 function calling 调用 KAGWeb 注册的工具」。这个假设随 in-process agent loop 一起被移除了。现在：

- `AgentLoopRequest` 没有 `tools` 字段——后端收不到工具 schema；
- `ChatCapability` 不 import 也不使用 `ToolRegistry`；
- CLI 后端**只接收 prompt 字符串**，没有任何工具注入通道。

**决定性证据**：`ChatOrchestrator.get_tool_schemas()`（`orchestrator.py:179`）在全仓库**零调用者**。

**影响**：用户能开一个永远不生效的开关，管理员能授一个永远用不上的权限。这是**功能性误导**，比死代码更糟。

### P1-1 上下文窗口错配 —— ✅ 已修复

**现象**：agent loop 部署下历史预算被钉死在最低档。

**链路**：`executor.py` 把 `llm_config`（此处为 `None`）传给 `ContextBuilder.build()` → `_effective_context_window()` → `resolve_effective_context_window()` → 无模型名可匹配 → 回落 `DEFAULT_CONTEXT_WINDOW_FALLBACK = 16_384`（`llm/context_window.py`）→ 乘 `history_budget_ratio = 0.35`。

**结果：约 5,734 token 的历史预算**，喂给一个通常有 200K~1M 窗口的 Claude Code / Codex。

`LARGE_CONTEXT_MODEL_DEFAULT = 65_536` 的「大模型多给」兜底逻辑依赖**模型名匹配**（`KNOWN_LARGE_CONTEXT_MARKERS` 含 `claude`/`gpt-5` 等），而 agent loop 部署下模型名是空字符串——**永远走最低档**。

**影响**：长会话过早触发摘要甚至截断，丢掉本该保留的上下文。

**修复（2026-09-10）**：profile 新增 `context_window`，操作者填该后端的真实窗口。注入路径受**时序约束**约束——`executor` 建 context 时（`_run_turn` 内）capability 尚未解析 profile，故值经内部 payload 键 `agent_loop_context_window` 传递：

```
agent_loop 设置块 → request_preparer 提取 primary 的 context_window（非 0 才注入）
                  → payload["agent_loop_context_window"]
                  → executor 传给 builder.build(context_window_override=…)
                  → _effective_context_window 优先采用，并仍受 MAX_EFFECTIVE_CONTEXT_WINDOW 约束
```

要点：
- **管理员与非管理员都注入**——历史预算是运维问题，不是权限问题。
- 该键在 `TurnRequest.model_validate()` **之后**附加，因此不在公开 schema 内，也不被 `_request_snapshot_metadata` 持久化（有测试钉死这两点）。
- **未配置（0）时行为完全不变**：不注入，走原回落链。
- `0 = 未配置` 是刻意的语义，因此不能走 `_coerce_clamped_int`（它的下钳会把它变成 1,024，等于谎报一个窗口）。

### P1-2 模型选择器指向错误对象

`llm_selection`（`core/turn_request.py`）随轮次请求下发，前端在伙伴配置等界面仍在发送。但在 agent loop 模式下：

- `ChatCapability` 完全不读它（grep 无命中）；
- 它只影响 KAGWeb 自己的 LLM 层（标题、洞察、摘要）；
- 对 agent backend **零影响**。

用户换一个模型，对话行为**毫无变化**，且没有任何提示说明这一点。

> **✅ 已修复（2026-09-11，B 方案落地）**：`llm_selection` 现在解析为具体模型名随 `AgentLoopRequest.model` 下发到 backend——one-shot CLI 族经 `{model}` 占位符逐回合生效（选择 > profile.model > 后端默认）；HTTP 族请求体携带 `model` 键（上游消费前由 intellect-agent#126 跟踪）；ACP 无此协议字段。preset 以 `per_turn_model` 声明支持，`/api/auth/status` 的 `model_selector_enabled` 据此隐藏选择器——标题/洞察仍走同一选择（用户级控制保留）。

### P1-3 profile schema 没有 model 概念 —— ✅ 已修复

原字段集（`runtime_settings.py` 的 profile 归一化）：

```
id, name, preset, enabled, command, args, env,
url, turn_path, headers, api_key,
timeout_seconds, session_workspace, consult_enabled, workdir
```

无 `model`。CLI 后端只能靠 `args`（前端占位符正是 `--model\nbig-model\n{prompt}`）或 `env`；**HTTP 后端连塞的地方都没有**。

**修复（2026-09-10）**：新增 `model` 与 `context_window`。`model` 的空值语义是"用后端自己的默认"：

| family | `model` 如何生效 |
|---|---|
| CLI | `args` 里的 `{model}` 占位符被替换（`--model={model}` 与 `--model {model}` 均可）。**未配置 model 时，含 `{model}` 的整条参数被丢弃**——于是 CLI 用自己的默认，而不是收到一个字面占位符或裸 `--model` 标志。前端 helper 文本推荐 `--model={model}` 这种同条写法。 |
| HTTP（turn / runs） | 作为请求体字段发送；**未配置时不发该键**，既有部署的请求体逐字不变。 |
| ACP | 记录在实例上（ACP 无 argv 占位符可替换，握手协商 agent 会话）；保留供未来的 model-select 请求与诊断。 |

`model` **不**加入 `AGENT_LOOP_ENV_OVERRIDABLE_KEYS`：环境覆盖是部署级 pin 的场景（`KAGWEB_AGENT_LOOP_COMMAND` 等），而 model 是每 profile 的常规配置。

### P1-4 凭据双份，互不相通

见 §1.5。运营者要维护两套 Codex 认证，且不理解为什么登录了 KAGWeb 的 Codex 之后 `codex` 预设仍然不可用。

### P2-1 检测不覆盖认证

见 §1.6。检测无法回答运营者真正关心的问题：「这个后端能用吗？」

### P2-2 模型目录残留服务槽位

`SERVICE_NAMES`（`model_catalog.py:151-160`）声明 8 个服务，但：

| 服务 | 解析函数 | 消费方 |
|---|---|---|
| `llm` | `resolve_llm_runtime_config` | 9 个旁路调用点 |
| `task` | 经 `service_name=TASK_SERVICE` 复用 | 标题生成（`model_selection/tasks.py`） |
| `search` | `resolve_search_runtime_config` | `system.py`、`test_runner.py`、搜索设置页（`tools.py:21` 的消费方将随决策 3b 移除） |
| `tts`/`stt` | 有 | `voice/` |
| `imagegen` | 有 | `imagegen/` |
| **`embedding`** | **不存在** | **无** |
| **`videogen`** | **不存在** | **无** |

`embedding` 与 `videogen` 对不上任何解析函数，是 RAG / videogen 剥离后的残留槽位，仍出现在设置界面。

### P2-3 错误文案指向无效动作

`llm/config.py:200-202` 的提示是 *"No active LLM model is configured. Please set it in Settings > Catalog."*。在 agent loop 部署下，去 Catalog 配一个模型**不会**改变对话行为——真正的配置在 Settings → Chat → Agent Loop。

---

## 四、改进建议

### 阶段一：解除阻断（必须，改动小）——✅ 已落地

**1. 解耦轮次门禁与 LLM 授权**

不要让 `request_preparer` 硬编码 `has_capability_access("llm")`。让能力**声明自己需要什么资源**：

```python
# CapabilityManifest 增加
required_service: str = "llm"  # chat 在 agent-loop 模式下改为 "agent_loop"
```

`request_preparer` 据此判定：

```python
service = capability_manifest.required_service
if not has_capability_access(service):
    raise RuntimeError(f"No {service} access assigned to your account...")
```

配套：

- `empty_grant()`（`multi_user/grants.py`）增加 `"agent_loop"` 维度；
- `model_access.py` 增加 `agent_loop` 分支（复用 `has_capability_access` 的通用形状）；
- `ChatCapability.manifest` 声明 `required_service`，由 `_effective_required_service()` 按部署修正。

**这是收益最高的一项**：它把「多用户 agent-loop 部署」从「不可能」变成「可用」。

> **落地时的两处修正（2026-09-10）**，都源于"只看了资源需求、没看风险与路径"：
>
> 1. **CLI/ACP 族必须默认拒绝，而非默认允许**。该族 spawn 的本地进程以服务器 uid 运行且无沙箱，授权它等同授予本机代码执行；`intellect`（family=cli）又是 auto-primary 首选，默认安装即落在该族。故新增 `agent_loop_cli`（opt-in），与 HTTP 族的 `agent_loop`（默认允许）**方向相反**。
> 2. **检查必须覆盖所有路径**。第一版把门禁放在"未固定 `llm_selection`"的分支内，而该字段客户端可传——固定它即绕过。已移到分支外，并补 `start_turn` 级测试。

**2. 诚实化工具层**

在 P0-2 解决之前，**不要**在 UI 上把工具呈现为可用。二选一：

- **短期（诚实）**：agent loop 模式下隐藏工具开关与授权项，或明确标注「当前后端不支持」。**已落地**：`/api/tools`、`PUT /api/settings/enabled-tools`、设置页 Tools 分区与两个前端 lib 均已移除（工具层本体留待与合伙人一并处理）。
- **长期（打通）**：见阶段三。

### 阶段二：对接 agent backend 的模型（核心诉求）——✅ 已落地

**3. profile 增加 `model` 字段**

```jsonc
{
  "id": "default",
  "preset": "claude-code",
  "command": "claude",
  "model": "claude-sonnet-5",        // 新增
  "args": ["--model={model}", "{prompt}"],   // CLI：占位符替换
  "context_window": 200000,          // 新增：后端真实窗口
  ...
}
```

- **CLI 族**：`{model}` 占位符替换注入 argv。未配置 `model` 时**含 `{model}` 的整条参数被丢弃**（不是保留字面占位符，也不是留下裸 `--model`），于是 CLI 用自己的默认。前端 helper 因此推荐 `--model={model}` 这种同条写法。
- **HTTP 族**：请求体增加 `"model"` 字段（turn 与 runs 两个协议都加）；**未配置时不发该键**，既有部署的请求体逐字不变。
- **ACP 族**：记录在实例上备用（无 argv 占位符；握手协商 agent 会话）。

**4. 打通上下文窗口**

`ContextBuilder` 优先采用 profile 声明的窗口，而非从 `llm_config` 推导：

```python
def _effective_context_window(self, llm_config, context_window_override=None) -> int:
    return resolve_effective_context_window(
        context_window=context_window_override or getattr(llm_config, "context_window", None),
        model=...,
        max_tokens=...,
    )
```

实现补充（与本文初稿的设想不同，原因见下）：**值不是从 profile 直接取的**。`executor` 在 `_run_turn` 内建 context，早于 capability 解析 agent-loop profile，所以实际路径是「request preparer 提取 → payload 内部键 → executor 传入 override」。详见 P1-1 的修复说明。

这直接修掉 P1-1，且让「历史预算」这个决策重新归属于**真正消费历史的那一方**。

**5. 模型选择器要么生效，要么隐藏** —— ✅ 完成（2026-09-11，两条都做了）

- **映射（B 方案）**：`llm_selection` → `AgentLoopRequest.model`，one-shot CLI 族 `{model}` 逐回合生效；HTTP 族 body `model` 键已随请求发送，上游消费由 intellect-agent#126 跟踪；
- **隐藏（A 方案）**：preset 的 `per_turn_model` 标志 + `/api/auth/status` 的 `model_selector_enabled` 门控——不支持的后端（ACP/HTTP-待上游/未配置）选择器直接不渲染。

> 产品决策记录：存在"单 backend 多模型、按回合切换"的真实场景，故映射与隐藏同时落地；标题/洞察的模型选择保留用户级控制（同一选择继续驱动 KAGWeb 辅助 LLM 层）。

### 阶段三：收敛与清理

**6. 工具层：让 agent 自己暴露工具**

KAGWeb 不再持有工具实现，改为**桥接**：

- 若后端是 MCP-capable（Claude Code、Codex），KAGWeb 可作为 MCP **服务端**暴露工具，由后端自行发现与调用；
- `ToolRegistry` 保留为「向 MCP 暴露的定义源」，`AgentLoopRequest` 增加 MCP 端点信息而非工具 schema 列表。

这比「把 4 个工具塞进 prompt」更符合 agent-native 的架构方向，也复用了 KAGWeb 已有的 `services/mcp/`。

**7. 清理模型目录残留**（P2-2）

`SERVICE_NAMES` 移除 `embedding` / `videogen`，或在设置界面标注为「未实现」。

**8. 修正错误文案**（P2-3）

`NoModelConfiguredError` 的提示应区分场景：agent loop 部署下指向 Settings → Chat → Agent Loop。

**9. 检测增强**（P2-1）

CLI 检测可考虑增加**可选**的 `--version` 探测（当前刻意不做，是为了「side-effect free」）；更有价值的是在 profile 上增加一个「认证自检」按钮，明确告知运营者后端是否真的可用。

**10. 凭据打通（可选）**（P1-4）

让 `codex` agent-loop 预设复用 `codex_auth` 的 token：CLI 后端在子进程 env 中注入从 `codex_auth` 取得的凭据（而非让 CLI 读 `~/.codex`）。**注意**：这需要谨慎——`codex_auth` 的 token 绑定 ChatGPT 账户，注入子进程等同于把 KAGWeb 管理的凭据交给外部进程，应视为架构决策而非实现细节。

---

## 五、产品决策安排（8 条）

以下为已确定的产品方向，及其落地所需的代码改动与影响面分析。

### 决策 1：KAGWeb 不实现任何 agent backend，直接作为第三方 agent backend 的前端

**定位确认**：KAGWeb = agent backend 的 **Web 门面**。它提供对话界面、多用户、会话存储、权限、IM 通道；**不提供** agent 能力本身。

**当前代码的偏离**：KAGWeb 仍保留了一套完整的 in-process LLM 层与工具层，它们服务的是**已被移除的 in-process agent loop**。这套残留正是 §三各问题的根源。

**落地含义**：

| 组件 | 处置 | 依据 |
|---|---|---|
| `services/agent_loop/`（1,765 行） | **保留**，它是后端对接层 | 已有 CLI/HTTP 两族 + 中性事件 schema |
| `services/llm/`（9,476 行） | **大幅缩减**，非全删 | 9 个旁路调用点中仅少数仍需（见决策 5） |
| `kagweb/tools/` | **移除** | 见决策 3 |
| `capabilities/`（仅 `chat`） | **保留** | 编排骨架仍需要 |

**建议**：把「KAGWeb 是门面而非 agent 实现」写进 `ARCHITECTURE.md` 与 `AGENTS.md` 的定位段，作为后续裁剪的判据。

### 决策 2：Agent Backend 配置提为 Settings 顶级项

**现状（不合理之处）**：`agent-loop` 目前是 **Chat 分区的子项**（`settings-nav.ts:196-207`），父项「聊天」的副标题恰好是「Agent Loop、工具、能力与附件」（`:295-296`）——把「对话由谁驱动」这个**部署级决策**混在了对话偏好里。

**建议改法**：新增 Settings 顶级项 **Agent Backend**（或 Agent Harness），与 LLM / Chat / 知识库平级。

```
设置导航（改动后）
├── 模型与连接（LLM）      ← 决策 5 门控
├── Agent Backend          ← 新增顶级项（原 agent-loop）
├── 聊天
│   ├── 能力
│   ├── 起始建议
│   └── 附件
├── 知识库（文档解析）
└── ...
```

**改动点**：

| 位置 | 改动 |
|---|---|
| `settings-nav.ts:196-207` | 从 `CHAT_CHILDREN` 移到顶层数组 |
| `settings-nav.ts:295-296` | 父项副标题去掉「Agent Loop」 |
| `settings-nav.ts:293` | 「聊天」分区语义随之收窄 |
| `settings-nav.ts:374` | 配置文件映射 `"agent-loop": "data/user/settings/system.json"` 保留（路径不变） |
| `api/routers/settings.py:1048` | 端点**不变**（仅前端导航调整） |

**注意**：`AgentLoopSettingsSection.tsx` 组件本身无需重写，只是挂载位置与导航层级变化。

### 决策 3：移除 Skills 与 Tools

两者的移除成本**差异很大**，因为 MCP 以工具协议为底座。

#### 3a. Skills 移除

**结论：运行时早已移除，剩余为孤儿资产与死字段。**

| 类别 | 位置 | 处置 |
|---|---|---|
| 孤儿资产（5 个 `SKILL.md`） | `kagweb/skills/builtin/{docx,pdf,pptx,xlsx,skill-creator}/` | **删除文件**；`pyproject.toml:253-254` 的注释需改写（见下方 ⚠ 打包陷阱） |
| 死字段 | `core/context.py:115` `skills_manifest`、`core/turn_request.py:88` `skills`、`grants.py:28` `"skills": []` | 删除 |
| 死模型 | `api/routers/multi_user.py:89-93` `SkillInstallPayload` | 删除 |
| 孤儿 prompt hints | `tools/prompting/hints/{en,zh}/read_skill.yaml` | 删除 |
| 前端死 UI | `GrantEditor.tsx` skills 段、`types.ts`、`start-turn.ts`、`buildStartTurnInput.ts` | 删除 |
| 生成契约 | `contracts/generated/{api.ts,turn-protocol.ts}`、`contracts/schema/*.json` | 重新生成 |
| 词条 | `web/locales/{en,zh}/app.json` 约 20 处 | 清理 |

**⚠ 两处不是残留，需单独决策**：

1. **`migrate_legacy_skills` 仍在运行**（`services/persona/service.py:305-339`，由 `get_persona_service():349-357` 在首次访问时触发）。它读**用户工作区**的 `skills/<name>/SKILL.md`，转成 `personas/<name>/PERSONA.md`，并 `shutil.rmtree` 源目录。移除 Skills 等于删除这个一次性迁移——需确认存量部署是否还有未迁移的工作区。
   *注：`data/user/workspace/` 下已无 `skills/` 目录，说明本机早已迁移完毕。*

2. **`cli_backend.py:44-57` 的 `Skill` → `read_skill` 映射是活跃的**。Claude Code 后端发出 vendor 工具 `Skill` 时，KAGWeb 翻译成 `read_skill` 并让前端显示「Reading skill」——而 KAGWeb 并无此工具。移除 Skills 时**必须同时决定这个 vendor 工具的展示名**，否则前端会显示一个不存在的工具。
   *相关测试：`tests/services/agent_loop/test_backends.py:499-500`、`web/tests/agent-loop-trace.spec.tsx`、`web/tests/fixtures/agent-loop-round-events.json`。*

**`skill-slug.ts` 的处置随决策 6 反转**：本文初稿建议「改名为 persona 中立的 helper 并保留」，理由是 `PersonasSection.tsx` 用它做人格名校验。**既然人格功能整体移出（决策 6），该文件与其测试一并删除。** 详见 §五决策 6。

> **⚠ 打包规则（经决策 6 修正后的结论）**：`kagweb/` 下被 git 跟踪的 `.md` 文件**共 8 个**——5 个 `skills/builtin/*/SKILL.md` + 3 个 `services/persona/presets/*/PERSONA.md`。决策 3 删前者、决策 6 删后者，**8 个文件将全部消失**，届时 `pyproject.toml:259` 的 `"**/*.md"` 规则成为空规则，可随之移除，`:253-254` 的注释也一并删除。
>
> *更正记录*：本文初稿曾建议「保留 `"**/*.md"`，因为 PERSONA.md 依赖它」。该建议在**人格保留**的前提下成立；决策 6 移除人格后，前提不再成立。**删 `.md` 规则前必须先确认决策 6 已落地**，否则会静默丢失内置人格（`PRESETS_DIR.is_dir()` 为假 → `seed_presets()` 返回空，无报错）。

#### 3b. Tools 移除

**结论：4 个工具本身不可达（§P0-2），但其底座被 MCP 复用——移除边界必须切准。**

**可安全删除（工具专属）**：

- `kagweb/tools/` 全部（含 `builtin_specs.py`、`builtin/`、`prompting/`、以及孤儿 `web_fetch.py`、`question_bank.py`）
- `api/routers/tools.py`（整个 `/api/tools` 路由）
- `api/main.py:44-69` `validate_tool_consistency()` + `:478` 路由挂载
- `runtime/orchestrator.py:21,58,170-180`（`_tool_registry` + `list_tools()` + `get_tool_schemas()`）
- `services/settings/interface_settings.py:19,124-148`（`sanitize_enabled_tools` 等）
- `api/routers/settings.py:76,122,431,1950-1953`（工具开关端点）
- 授权字段 `grants.py:47` `enabled_tools` + `tool_access.py:44-52` `allowed_optional_tools()`
- 前端：`ToolsSettingsSection.tsx`、`tools-settings.ts`、`tool-availability.ts`、导航项
- i18n：`kagweb/i18n/metadata_i18n.py:40-56`、`web/locales/*/app.json` 工具词条

**必须保留（MCP 底座）**：

`core/tool_protocol.py`、`runtime/registry/tool_registry.py`（仅剥离 `load_builtins`/内置耦合）、`runtime/registry/deferred_tools.py`、`runtime/registry/scoped_registry.py`、`runtime/providers/*`、`services/mcp/session_state.py`、授权字段 `mcp_tools` / `cli_apps`。

**❌ 不可删**：`services/search/`（**3,276 行**）。它被 `api/routers/system.py:61-64`（健康检查）、`services/config/test_runner.py`、搜索设置页独立使用。**只有** `kagweb/tools/web_search.py` 这个 re-export shim 是工具专属的。

> *更正*：本文初稿此处写作「4,812 行」，该数字实为 `services/parsing/` 的行数。

**⚠ 必须重新指向（否则 import 断裂）**：

| 位置 | 问题 |
|---|---|
| `services/partners/commands.py:11` | `from kagweb.tools.builtin import default_optional_tools` |
| `services/partners/runtime.py:727-731` | 同上 |
| `services/setup/init.py:83-85` | `agents.yaml` 默认 `tools.brainstorm` 块 |
| `services/config/loader.py:213,238` | `get_agent_params("brainstorm")` |
| `config/constants.py:12-21` | `VALID_INVESTIGATE_TOOLS` / `VALID_SOLVE_TOOLS`（已失效的 solve/investigate 列表） |
| `kagweb_cli/chat.py:182-187` | CLI `/tool on\|off`（流向永不消费的 `enabled_tools`） |
| `kagweb_cli/plugin.py:24-56` | `plugin list/info` 读 `get_tool_registry()` |

**测试注意**：`tests/tools/test_mineru*.py` 名字在 `tools/` 下，但测的是 `services.parsing.engines.mineru`——**不要随 tools 一起删**。

#### 3c. 附带发现：MCP 工具面同样是休眠的

调查中发现一个**代码注释失准**：`services/mcp/manager.py:300` 声称 MCP 工具「reach a turn through the scoped registry's overlay」，但构建该 overlay 的 `build_tool_view()`（`runtime/providers/view.py:70`）**零生产调用者**（已实测确认），`ScopedToolRegistry` 也仅被它使用。

即：**MCP 工具注册进了进程注册表，却没有任何代码把注册表里的工具变成后端可见的工具面**。MCP 的**配置/连接/凭据**部分（~2,892 行）功能完整，但工具**投递**这一环是断的。

这与 §P0-2 是同一个根因：**工具面需要一条送到 agent backend 的通路**，而该通路随 in-process loop 一起没了。若采纳决策 1（KAGWeb 作门面），MCP 的定位应重新设计（见 §P2-6：让 KAGWeb 作 MCP **服务端**暴露给后端）。

### 决策 4：学习 / 研究相关表述清单

> 完整用语盘点由并行调查产出，以下为**已亲自复核**的高置信项。分类标准：**ACTIVE** = 真实代码路径 / 用户可见；**RESIDUAL** = 无行为的残留；**NAME-ONLY** = 名字误导但功能通用。

#### ACTIVE（真实功能，需决策）

**A 类：用户直接可见的表述**——优先决策项。

| # | 位置 | 内容 | 说明 |
|---|---|---|---|
| 1 | `web/components/sidebar/nav-entries.ts:6,29,39` | `TopNavGroupId = "learning"`（:6）；导航项 **"Learning Agent"**（:29，→`/chat`）、**"Learning Space"**（:39，→`/space`）；分组名 **"Learning"**，图标 `GraduationCap`（毕业帽） | **最用户可见的一项**。整站主导航的学习定位 |
| 2 | `web/components/space/SpaceDashboard.tsx:21-24,189` | "Learning Space dashboard" | `/space` 门户标题 |
| 3 | `services/persona/presets/peer/PERSONA.md` | "curious **study partner**" | **打包预设**，新装即播种 —— **已由决策 6 决定整体移除** |
| 4 | `services/persona/presets/teacher/PERSONA.md` | "patient **Socratic tutor**"…"help the **learner**" | 同上；且是学习者授权的 `locked_persona` —— **已由决策 6 决定移除** |
| 5 | `services/persona/presets/research-assistant/PERSONA.md` | "rigorous **research assistant**"…"survive **peer review**" | 同上 —— **已由决策 6 决定移除** |
| 6 | `services/partners/manager.py:1410` | 默认 SOUL 模板 **"Math Tutor"（:1440）/ "Research Helper"（:1491）/ "Language Tutor"（:1517）** | `_load_souls()`（:1340）在 `_souls.yaml` 缺失时调用 `_seed_default_souls()`（:1343 → :1362）——**新装即播种** |

**B 类：学习者子系统——剥离后存活的**最大一块** DeepMentor**

这是「学习层」中**唯一完整保留**的部分：后端 + 前端 + 测试俱全。

| # | 位置 | 内容 |
|---|---|---|
| 7 | `multi_user/learner_profile.py:9` | 档案字段 `age` / `grade_level` / `curriculum` / `reading_level` / `explanation_style` |
| 8 | `multi_user/guardians.py`（整个文件） | 监护人授权：`authorize_guardian` / `guardian_can_access` / `GUARDIAN_PERMISSIONS` |
| 9 | `api/routers/multi_user.py:173-427` | `/guardians`、`/me/guardianships`、限制与凭据重置端点 |
| 10 | `api/routers/auth.py:990-1084,1146-1171` | 自助 + 管理员的 learner-profile 端点 |
| 11 | `services/session/turns/executor.py:363-370,473` | `learner_profile_prompt` **注入系统提示词**（`preset=="learner"` 账户） |
| 12 | `web/features/settings/sections/LearnerProfileSettingsSection.tsx` | 设置分区「学习档案」（`learnerOnly`） |
| 13 | `web/features/settings/sections/GuardianSettingsSection.tsx` | 设置分区「监护人」（`showGuardianOnly`） |
| 14 | `web/app/(admin)/admin/users/page.tsx:395-396,477-486` | 「Learner」预设徽章 + `LearnerProfileEditor` + `lockLearningPolicy` |
| 15 | `multi_user/models.py:10` | `AccountPreset = Literal["standard", "learner", "custom"]` |
| 16 | `grants.py:16-19,63-96,135-157,204-243` | `LEARNING_*` 常量、`_normalize_learning_policy`、`learner_grant()` |
| 17 | `multi_user/device_credentials.py` | 设备凭据（面向本地学习者账户） |

#### ACTIVE-RESIDUAL 混合：两个需澄清的子系统

**18. 小导师（`selection_tutor`）——功能通用，命名教育化**

`selection_tutor_context` 是一套**完整的「针对选中段落提问」侧边栏线程**，功能与学习无关，但名字带 tutor：

- 后端：`_turn_runtime_shared.py:459-633`（提取/解析/格式化）、`executor.py:202-208,448-450`（注入 `sidebar_system_context`）、`request_preparer.py:198-233`（设 `session_kind: "selection_tutor"`）
- 前端：`web/lib/selection-tutor.ts`、`ChatStateAdapter.tsx:2104-2130`、「Little Tutor」/「Tutor threads」UI

**建议**：功能保留，命名去教育化（如 `selection_context` / `passage_thread`）。

**19. 答题库（`question_bank` / quiz）——读路径孤立，写路径活跃**

调查中此处出现过矛盾结论，实测澄清如下：

| 方向 | 状态 |
|---|---|
| **写** | **活跃**——`POST /api/sessions/{id}/quiz-results`（`api/routers/sessions.py:318`），前端 `web/lib/session-api.ts:240` 调用，写入 `notebook_entries` |
| **读** | **孤立**——`has_question_bank_entries()` 在 `sqlite_store.py:2379` 定义，**零调用者**；`question_bank_stats()` 的唯一调用者是 `tools/question_bank.py:127`，而该工具未注册（`builtin_specs.py:45-53` 仅 4 个） |

即：**数据被写入，但没有任何 UI 或工具把它读出来**。`question_bank.py` 的独立调查结论（"orphaned"）在此得到确认。

#### 高优先级 RESIDUAL（会走进死路的活代码）

| # | 位置 | 内容 | 风险 |
|---|---|---|---|
| 20 | `grants.py:148` | `default_capability: "immersive_reading"` | **该能力不存在**（`BUILTIN_CAPABILITY_CLASSES` 只有 `chat`）。因 `LEARNING_CAPABILITIES` 是硬编码集合，校验能通过——任何将来消费此值的代码都会失败 |
| 21 | `web/features/multi-user/components/GrantEditor.tsx:43-44` | `allowed_capabilities: ["chat", "immersive_reading"]` | UI 仍可选不存在的能力 |
| 22 | `web/features/multi-user/types.ts:22-23` | 联合类型写死 `"chat" \| "immersive_reading"` | 同上 |
| 23 | `grants.py:227-228` | 校验只对 `LEARNING_CAPABILITIES` 做，**不与真实能力注册表对账** | 校验形同虚设 |
| 24 | `services/session/sqlite_store.py:84` | `ASSESSMENT_SOURCES = {deep_question, mastery_path, immersive_reading, book}` | 用作持久化 `source` 字段的白名单（`:178`）。**4 个名称全部对应已移除的子系统**（能力注册表仅有 `chat`）；因读路径本身已孤立（见 #19），当前无实际影响 |
| 24b | `web/features/settings/navigation/settings-nav.ts:181-192` | `videogen` 服务导航项（「文生视频」，描述提到已移除的 chat videogen 工具） | 与 §P2-2 同源 |

**注**：`immersive_reading` 目前**不在轮次时被消费**（已核实：无代码读 `default_capability` 决定 `active_capability`）。因此 #20 是**潜伏陷阱**而非活跃故障——但同一根因（硬编码集合不对账注册表）值得一并修掉。

#### 其余 RESIDUAL（低风险清理）

按移除层的来源归类，均为无行为残留：

| 来源 | 位置 |
|---|---|
| **mastery / reading / course 残留** | `web/lib/workspace-mode.ts`（`WorkspaceMode` 类型）、`web/lib/mastery-session.ts`、`web/lib/session-archive.ts:19-124`（`buckets.mastery/reading`）、`web/components/space/ArchivedConversations.tsx:141`（渲染永不为空的分区）、`web/features/capabilities/presentation.tsx:149-178`（`course_study`/`mastery_path`/`immersive_reading` 目录项） |
| **孤儿 prompt hints** | `tools/prompting/hints/{en,zh}/mastery_*.yaml`（**无对应工具实现**）、`write_note.yaml`、`list_notebook.yaml`、`read_source.yaml` |
| **线上字段指向已移除界面** | `core/turn_request.py:29-104` 的 `NotebookReference` / `mastery_path_id` / `course_id`；`web/features/chat/model/start-turn.ts`、`buildStartTurnInput.ts:76-98`；契约 schema |
| **TutorBot 时代迁移串** | `services/partners/manager.py:1275-1332`（`_migrate_legacy_tutorbot`）、`:1547-1556`（`_TUTORBOT_SEED`）、`_LEGACY_SOUL_ID_ALIASES` |
| **失效设置键** | `api/routers/settings.py:108`、`services/setup/init.py:24-25` 的 `learnResearch: ["/question","/solver","/research","/co_writer"]` |
| **MinerU 试卷残留** | `services/parsing/engines/mineru/local.py:68,115,258-277`（`reference_papers` 目录、`parse_exam_paper_to_templates`） |
| **死词条（量最大）** | `web/locales/{en,zh}/app.json`。实测：两份文件各 **4,957** 个键，其中 `guidedLearning.*` **86 个**、键名含「learning」的 **150 个**（含活跃项如 "Learning Agent"）。另有 `"Deep Research Lab"`、`"Video Learning"`、`"Mastery Path"`、`"Immersive Reading"`、`"Course Study"`、`"Question Bank"`、`"Mimic Exam Paper"`、`"EduHub"`、`"Skill Center"` 等散落死串。**约 250 个**为需逐个判定的量级估计，非精确计数 |

#### NAME-ONLY（名字误导，功能通用）

| # | 位置 | 建议 |
|---|---|---|
| 25 | `api/routers/auth.py:402` `require_learning_surface` | 它是**通用鉴权门**——`api/main.py:425-428` 的 `_auth` 依赖，施加于**全部受保护路由**。其自身 docstring 已说明「学习界面随能力层移除，该依赖保留为唯一鉴权接缝」。**改名 `require_authenticated`，不要移除** |
| 26 | `web/lib/trace-mode.ts:13-30` | `TraceMode = "learner" \| "expert"`——追踪信息的详略开关，与学习无关 |
| 27 | `services/llm/types.py:11-51` | `TutorResponse` / `TutorStreamChunk`（`LLMResponse = TutorResponse`）——通用 LLM 类型用了 TutorBot 的命名 |
| 28 | `web/lib/skill-slug.ts` | 被 `PersonasSection.tsx` 用于人格名校验。**随决策 6 一并删除**（本文初稿曾建议「改名保留」，该建议已被决策 6 推翻） |

#### 重要区分：研究类 ≠ 残留

调查发现 `research` / `paper` / `citation` 的大量命中**属于 KAGWeb 的真实产品域**，不应据此清理：

- `deep_research` 能力（`web/features/capabilities/presentation.tsx:122-127`）
- `paper_search` / `ArxivSearchTool`（`tools/paper_search_tool.py`）
- 搜索引用体系：`services/search/types.py:13` `Citation`、`url_citation`、前端 `markdown-display.ts:382-554` 的引用链接化
- `services/path_service.py` 的 `research` / `deep_research` 目录

**但这些同样受决策 3 影响**：`paper_search` 是 4 个内置工具之一，在 agent-backend 模式下不可达；引用体系是否保留取决于工具层如何重新定位（§五 3c）。

#### 与「已移除」的权威对照

`tests/api/test_canonical_route_surface.py:30-40` 维护了退役路由前缀清单，是判断「哪些已移除」的权威来源：`/api/learning`、`/api/reading`、`/api/mastery-paths`、`/api/question*`、`/api/notebook(s)`、`/api/courses`、`/api/skills`、`/api/subagents`、`/api/video-learning`、`/api/visualizers` 等。前端另有 `web/tests/no-v1-chat-surface.test.ts`、`space-dashboard-capability-gate.test.ts` 作为守护测试。

### 决策 5：仅 Intellect 社区版/企业版启用 LLM 设置

**规则**：当且仅当 primary agent backend 为 `intellect` 或 `intellect-team` 时，启用 Settings 中的 LLM（模型与连接）分区；其余后端一律不呈现 LLM 设置。

**判定基元已有**：`AGENT_LOOP_INTELLECT_PRESETS = frozenset({"intellect", "intellect-team"})`（`runtime_settings.py:97`），已用于自动选择 primary 的优先级。

> **⚠ 该集合已不完整（2026-09-10 补记）**。远端在此期间合入了大量 agent-loop 变更，Intellect 现在有**三个**预设，而集合只含两个：
>
> | 预设 | family | 传输 | 是否在集合内 |
> |---|---|---|---|
> | `intellect` | **cli** | ACP（`intellect acp`，长驻子进程） | ✅ |
> | `intellect-team` | http | turn（**应改为 runs，见决策 8 D1**） | ✅ |
> | **`intellect-runs`** | http | runs（`/v1/runs` + SSE） | ❌ **缺失** |
>
> `intellect-runs` 的描述是「Intellect **community** api_server over the run endpoints… for containerized deployments that cannot spawn the CLI」——**同属 Intellect 社区版**，按决策 5 的规则应一并启用 LLM 设置。落地前必须把 `intellect-runs` 加入该集合（或改为按 family/名称前缀判定，而非硬编码枚举）。

**另一处必须核对的前提**：`intellect` 预设已从 http **改为 CLI/ACP 族**（`command="intellect"`、`base_args=("acp",)`、`transport="acp"`）。这影响决策 5 的**理由**（见下）与 §1.4 的凭据模型——ACP 子进程走 `cli_backend._build_child_env` 的**同一白名单**（`acp_backend.py:495-497`），`uses_workdir = True`（`:452`），信任边界与 one-shot CLI 一致。

**需要新增的**：前端目前拿不到**解析后的 primary preset**——`_agent_loop_payload()`（`api/routers/settings.py:1048-1101`）返回 `settings` / `effective` / `auto_primary` / `presets` / `bounds`，但不含「谁是 primary 及其 preset」。

**实现方案**：

1. **后端**：`_agent_loop_payload()` 增加解析字段，例如
   ```python
   "effective_primary": {
       "id": <profile id or "">,
       "preset": <preset name or "">,
       "family": <"cli"|"http"|"">,
       "llm_settings_enabled": <preset in AGENT_LOOP_INTELLECT_PRESETS>,
   }
   ```
   判定逻辑应复用 `resolve_primary_profile()`，与真实轮次行为保持同源。

2. **前端**：`SettingsAccess`（`features/settings/navigation/settings-access.ts`）已有 `hideAdminOnly` / `showLearnerOnly` / `showGuardianOnly` 三个门控位，扩展一个：
   ```ts
   enableLlmSettings: boolean;   // 由 agent-loop payload 的 llm_settings_enabled 驱动
   ```
   用于门控导航顶级项 `models`（`settings-nav.ts:274`）及其 8 个子项。

**为何这个规则是合理的**：Intellect 是唯一由运营者自托管、因而需要运营者自带模型凭据的后端族（社区版/企业版）。Claude Code / Codex 等 CLI 后端自带登录态；hermes / agentscope 等 HTTP 服务的模型由服务端自己配置——**对它们呈现 LLM 设置是误导**，正是 §P1-2「模型选择器指向错误对象」的同类问题。

> **理由的适用性已变化（2026-09-10 补记）**：Intellect 社区版现在是 **CLI/ACP** 后端（`intellect acp`），而 CLI 族**自带登录态**（`~/.claude`、`~/.codex` 模式）——这与「需要运营者自带模型凭据」的原始理由**不再直接对应**。企业版（`intellect-team`）仍是自托管 HTTP 服务，理由依然成立。
>
> 这动摇了规则的一致性：若判据是「自托管 HTTP 服务」，则 `intellect`（ACP）与 `hermes`/`agentscope`（HTTP）的划分需要重新审视。**建议在落地前重新确认决策 5 的判据**——是按品牌（Intellect）还是按「是否为自托管 HTTP 服务」。本文不擅自改判，仅标记该冲突。

**联动影响**：

- 关闭 LLM 设置后，9 个旁路调用点中的**设置类**（`system.py:459` 测试连接、`settings_spec.py:516` 保存探测）自然失去入口——**方向正确**。
- 但**后台类**调用点仍会执行：会话标题（`title_service.py:124`）、轮次洞察（`:245`）、**历史摘要**（`context_builder.py:391`）。若不配模型，这三者按 §二的降级列正常退化（摘要退化为截断）。**建议**：在 Agent Backend 设置页明确说明这一后果，或对非 Intellect 后端默认关闭轮次洞察。

### 决策 6：人格（Persona）整体移出 KAGWeb

**规则**：人格**不在 KAGWeb 内配置**，一律从 agent backend 获取。

**现状**：KAGWeb 持有一整套 in-process 人格子系统，与决策 1（作门面）直接冲突。

**规模**：后端约 **535 行** + 前端界面 + 3 个打包预设。

| 层 | 位置 | 行数/说明 |
|---|---|---|
| 服务 | `services/persona/service.py` + `__init__.py` | 397 |
| 预设 | `services/persona/presets/{peer,teacher,research-assistant}/PERSONA.md` | 3 个文件 |
| API | `api/routers/personas.py`（`/api/personas`） | 138 |
| 前端 | `web/app/(utility)/space/personas/page.tsx`、`components/space/PersonasSection.tsx`、`lib/personas-api.ts` | — |
| 校验 helper | `web/lib/skill-slug.ts` + `web/tests/skill-slug.test.ts` | 仅被 `PersonasSection` 使用 |
| 播种 | `services/setup/init.py:188-191` | `seed_presets()` |

**移除清单**：

- 删除 `services/persona/`、`api/routers/personas.py`（含 `main.py:405,477` 的挂载）
- 删除 `executor.py:360-380` 的人格加载分支
- 删除 `TurnRequest` 的 `persona` 字段与前端对应链路（`buildStartTurnInput.ts:84,126`、`start-turn.ts:29,59`）
- 删除前端 personas 页面与 API 客户端
- **删除 `web/lib/skill-slug.ts` 及其测试**（随决策 6 反转本文初稿的「保留」建议）
- 删除 `pyproject.toml:259` 的 `"**/*.md"` 规则（见决策 3a 的打包说明）

**⚠ 三处必须单独处理，不能随目录一并删除**：

1. **`persona_context` 字段——其处置取决于决策 7**。本文初稿称「该字段是双源（人格 + 合伙人 SOUL），必须保留」。实测确认**只有这两个写入方**（`executor.py:375` 的人格、`partners/runtime.py:659-709` 的 SOUL），而**两者均被移除**（决策 6 + 决策 7）。因此该字段、`executor.py:372-380` 的加载分支、`capabilities/chat/capability.py:446` 的注入**全部可删**。
   *更正记录*：初稿的「必须保留」在**仅移除人格**的前提下成立；决策 7 一并移除合伙人后，该结论被推翻。

2. **`partners.py` 的两处 PersonaService 用法**（`:523-535` `_load_persona_markdown`、`:588-615` `soul_sources`）。当前合伙人的「灵魂模板」可直接从人格克隆（`soul_sources` 的注释写着 "Everything the create-wizard's soul step can start from"）——即**合伙人消费人格**，SOUL 的 `source: "persona"` 分支依赖人格工作区。这两处随决策 7 一并删除，**不再是「需单独确认的行为变更」**。

3. **学习者授权的 `locked_persona`**。`grants.py:18` `LEARNING_PERSONAS = {"teacher"}`、`:146` `learner_grant()` 设 `locked_persona: "teacher"`、`:213-215` 对其校验。人格移除后学习者授权的这一维度失去意义——**与决策 4 的学习者子系统去留一并决策**。注意这与 `learner_grant()` 的 `default_capability: "immersive_reading"`（已失效，见决策 4 #20）同源：**学习者授权里有多处指向已不存在对象**。

**落地顺序提示**：决策 6 与决策 3/4 在 `pyproject.toml` 打包规则上有依赖——**先落决策 6、再删 `.md` 规则**，否则会静默丢失内置人格（详见决策 3a 打包说明）。


### 决策 7：移除合伙人（Partners）与 IM 通道

**规则**：合伙人功能**只对接 agent backend**，而本项目已无 agent backend，因此**先移除**。将来恢复 IM 接入时，由 **agent backend 自己对接 IM** —— 即 KAGWeb 不为 IM 保留任何接缝。

**规模：约 42,000 行**，是目前最大的一次移除。

| 层 | 文件 | 行数 |
|---|---:|---:|
| `kagweb/partners/`（通道层） | 31 | 12,749 |
| `kagweb/services/partners/`（运行时层） | 15 | 5,165 |
| `kagweb/services/partner_groups/`（依赖前者） | 6 | 2,540 |
| API 路由（`partners.py` 1,753 + `partner_groups.py` 518 + `_partners_channel_schema.py` 176） | 3 | 2,447 |
| 前端 | 50 | 11,533 |
| 后端测试 | 29 | 7,605 |
| **合计** | **134** | **~42,000** |

**端点**：66 个（`partners` 44 + `partner_groups` 22），占全后端 217 个端点的 **30%**。移除后后端约 98,169 → **78,000 行**。

> *更正记录*：本文初稿此表写作「约 35,400 行」，且后端测试记为「1,189 行」。后者是 `find -iname "*partner*"` **只匹配文件名**导致的漏计——它漏掉了整个 `tests/services/partners/` 目录（21 个文件、5,151 行）。按目录重新统计为 **29 文件 / 7,605 行**。前端计数亦从 49 修正为 50（改用大小写不敏感的 `-ipath`）。

**本机状态**：`data/partners/` 下仅有 `_runtime/`，**无 `_souls.yaml`、无 `config.yaml`** —— 该功能从未被实际使用。

**通道**（16 平台 / 17 模块）：dingtalk、discord、email、feishu、matrix、mattermost、mochat、msteams、napcat、qq、slack、telegram、wecom、weixin、weixin_qr、whatsapp、zulip。

#### ⚠ 必须先做：`safe_filename` 迁移（会打断核心服务）

```
kagweb/services/storage/attachment_store.py:38
    from kagweb.partners.helpers import safe_filename
```

**模块级导入**，删目录即 `ImportError`，且 `attachment_store.py` 是**普通聊天的附件上传**服务，与合伙人无关。

- `safe_filename`（`kagweb/partners/helpers.py:36`，通用路径字符清理）**必须先移到 `kagweb/utils/`**。
- 同文件其余 5 个 helper（`detect_image_mime`、`ensure_dir`、`split_message`、`split_markdown_table_row`、`convert_markdown_table_to_labeled_rows`）经核实**仅合伙人内部使用**，可随目录删除。

#### 保留项（通用基础设施，不可随目录删）

| 项 | 位置 | 原因 |
|---|---|---|
| `BackgroundLeaderSupervisor`（整个文件） | `runtime/background_leader.py` | **零合伙人引用**；其 `recovery_callback`（`container.recover_once`）是**轮次恢复**，多 worker 部署必需 |
| `EventBus` | `events/event_bus.py` | 全局总线，`runtime/orchestrator.py:153-168` 独立向其发布 `CAPABILITY_COMPLETE` |
| `allowed_builtin_tools` 字段 | `core/context.py:107` | **双用**：合伙人与 selection-tutor（`executor.py:450`）都设置它——仅删除合伙人那侧的注释 |
| grants 引擎 / multi-user 路由 | `multi_user/grants.py` 等 | 仅摘除 `"partners"` 这一个字段（`:34`、`:120`） |
| `get_owner_path_service` / `get_owner_secrets_dir` | `multi_user/paths.py` | 普通用户仍需要；仅删 partner 分支（`:175,183-186`） |
| `executor.py:450` 的 `allowed_builtin_tools=[]` | — | 属 selection-tutor，与合伙人无关 |

**`main.py` 的简化**：删 `_start_partners`（`:165-170`）/ `_stop_partners`（`:172-176`）与 `_handle_background_command`（`:179-210`，**32 行**）；`BackgroundLeaderSupervisor`（`:213`）改用 `start_callbacks=[]`、`stop_callbacks=[]`、`control_callback=None`，**保留 `recovery_callback=application_container.recover_once`**。

#### 可连带删除的后台命令子协议

| 项 | 位置 | 依据 |
|---|---|---|
| `BackgroundCommandKind` 全部 4 个取值 | `coordination/types.py:37-41` | 3 个 partner + `CRON_RELOAD`。**实测 `find kagweb -iname "*cron*"` 无任何结果——cron 模块不存在**，该取值零消费者 |
| `submit/read/acknowledge_background_command` | `protocol.py`、`memory.py`、`redis.py` | `submit_background_command` 全仓库**唯一调用方**是 `partners.py:142` |

> *注*：并行调查曾断言「`CRON_RELOAD` 仍被 cron 子系统使用」，经复核为**误**——cron 模块已随卫星子系统移除，该枚举值是死代码。

#### 其他随删项

- `multi_user/partner_access.py`（154 行，整模块合伙人专用）
- `outputs.py:42-68` `_resolve_partner_output`（合伙人文件回退；普通用户路径 `:21-39` 保留）
- `settings.py:486-488` 的 `is_partner_user_id` 拒绝分支（Codex 登录端点）
- `source_inventory.py` 的合伙人会话语料引用（**50 处**提及，集中在 `:55`、`:117-146`、`:275-292`、`:392-408`、`:473-482`、`:562-658`）、`_turn_runtime_shared.py`、`turns/executor.py`、`request_preparer.py`、`core/turn_request.py:82` 的 `partner_group_references` 全链路
- `kagweb_cli/partner.py`（103 行）+ CLI `partner` 子命令
- `pyproject.toml` 的 `partners` extra（`:137-141`）、`tutorbot` extra（`:193`）、matrix extras（`:195`）；`requirements/partners.txt`
- 前端：5 个路由页（`web/app/(workspace)/partners/**`）、`components/partners/**`（**32 个文件**：18 个顶层 + `group/` 子目录 14 个）、5 个 API client（`partner-chat-draft.ts`、`partner-draft.ts`、`partner-groups-api.ts`、`partner-session.ts`、`partners-api.ts`）、7 个测试；并摘除 `ChatMessageList.tsx`、`TracePresentation.tsx`、`GrantEditor.tsx`、`capability-routes.ts`、`ContextBudgetChip.tsx` 中的合伙人分支

#### 连锁简化：`persona_context` 变为可整体移除

见决策 6 第 1 项——决策 7 使该字段的两个写入方**全部消失**。

### 决策 8：Intellect 对接全面校正（**本文件最高优先级发现**）

**背景**：为核实「`intellect-team` 是否支持审批」，克隆了 `gitee.com/wustbd/intellect-team`（浅克隆 HEAD `9dcfdfa`）逐项比对。结果推翻了本文此前关于 Intellect 对接的多处判断。

**对齐范围（已决策）**：**只对齐 Rust**。Rust 是权威且活跃演进的主版本；Python 版待 Rust 成熟后逐步放弃，kagweb **不做双版本兼容**。

**Intellect 有两个实现，Rust 是权威源**。该仓库自带一份 `docs/agentui-alignment/intellect-team-alignment-requirements.md`（由 AgentUI 团队提出），其中明确：

> BFF SSE 解析器 `parse-intellect-enterprise-run-events-sse.ts` 注释明确引用 Rust `api_server.rs` 作为权威源
> **Rust `api_server.rs`（8032 行）已超越 Python `adapter.py`（5611 行）成为主版本**

且文件列出 P0 级问题：**Python 版是否仍在维护（P0）** 尚且待 Intellect-Team 确认，超期后「AgentUI 将启用双版本兼容兜底方案」。

##### A. Rust 内部映射：`map_event_to_sse`（`api_server.rs:2095-2131`）

| SSE 行 | `type` | 其余字段 |
|---|---|---|
| （无） | `assistant.delta` | `text` |
| （无） | `reasoning.delta` | `text` |
| （无） | `thinking.progress` | `elapsed_s`, `silent_s` |
| `event: tool.progress` | `tool.started` | `tool_id`, `name`, `arguments` |
| `event: tool.progress` | `tool.completed` | `tool_id`, `name`, `result`, `duration_s` |
| `event: clarify` | `clarify` | `question`, `choices`, `clarify_id` |
| （无） | `assistant.completed` | `response`, `session_id` |
| `event: error` | — | `message` |

**关键**：除 `tool.progress` / `clarify` / `error` 外，**其余事件没有 SSE `event:` 行**——只有 `data: {"type": ...}`。事件的区分靠 JSON 体内的 `type` 字段。

##### B. 两个实现与 kagweb 翻译器的对照

kagweb 的 `RunsAgentLoopBackend._translate_run_event`（`http_backend.py:460-535`）读的是 `obj["event"]`。实测：

| 语义 | kagweb 期望 | Rust（权威） | Python 当前 |
|---|---|---|---|
| 文本增量 | `event=message.delta`, `delta` | `type=assistant.delta`, **`text`** | `event=message.delta`, `type=assistant.delta`, `delta` |
| 推理增量 | `event=reasoning.available`, `text` | `type=reasoning.delta`, **`text`** | `event=message.delta`, `type=reasoning.delta`, `text` |
| 工具开始 | `event=tool.started` | `event=tool.progress`, `type=tool.started` | ✅ `event=tool.progress`, `type=tool.started` |
| 工具结束 | `event=tool.completed` | `event=tool.progress`, `type=tool.completed` | ✅ 同上 |
| 澄清 | **无分支** | `event=clarify`, `type=clarify` | `event=clarify`（**但无响应端点**） |
| 心跳 | 无分支 | `type=thinking.progress` | `event=thinking.progress` |

**后果**：

- **Rust（权威）下**：文本与推理**全部静默丢弃**——kagweb 既收不到 `event=message.delta`，也不认 `type=assistant.delta`；工具事件可用；`clarify` 无分支。
- **Python 下**：文本可用（`delta` 字段匹配）；推理静默丢弃；工具事件可用；`clarify` 无分支。
- 因为 `_translate_run_event` 末尾 `return []`，**不认识的事件被静默忽略，不报错**——这是最危险的地方：对接失败不产生任何诊断信号。

#### 关联缺陷

**D1（P0）`intellect-team` 预设指向不存在的端点**

```python
AgentLoopPreset(
    name="intellect-team", family="http", description="Intellect enterprise (team) agent service."
)
# → protocol 默认 "turn"、turn_path 默认 "/agent/turn"
```

- 实测 `grep -rn "agent/turn"` 在 intellect-team 全仓库**无任何结果**——该端点不存在。
- 必须改为 runs 协议。`intellect-runs`（社区版）已正确配置（`turn_path="/v1/runs"`、`protocol="runs"`），企业版应同构。
- **「intellect-team 支持审批」在 kagweb 中从未生效**——本文此前称「turn 不支持审批」，定性不完整：**服务端支持，kagweb 走错了通道**。

**D2（P0）`clarify` 全链路缺失**

- 用户问：clarify 是否对应 kagweb 的 `ask_user`？**意图相同，机制不同，且 kagweb 实现不全。**
- 相同之处：都是「agent 向用户提问并等待答复」。kagweb 的 `capability.py:872-935` 已把审批请求映射成 `ask_user` 形状的卡片（`_approval_question`），注释即写「Approval requests — the agent-loop flavour of ask_user」，且 `ask_user` 与 `approval_request` **共用同一套卡片渲染器**（`capability.py:364-370`）。
- **但 kagweb 没有 clarify 的响应通道**：`protocol.py` 有 `respond_clarify` 抽象方法，而 `RunsAgentLoopBackend` **未实现**（只有 `respond_approval` / `cancel`）。Rust 侧端点是 `POST /v1/chat/completions/{session_id}/clarify`，body `{clarify_id, answer}`。
- 结论：**若要对齐，clarify 可复用 `ask_user` 的卡片与答复通路**（把 question/choices 映射成 `_approval_question` 那样的卡片，答复经新的 `respond_clarify` 发回）。但需先确认走 Rust 还是同时兼容 Python——**Python 版根本没有这个 HTTP 端点**（两个实现都存在 `clarify_channels`，只是暴露方式不同：Rust 自建路由，Python 停在函数层）。

**D3（P1）`run.cancelled` kagweb 已处理**（更正）

本文第三轮评审曾疑此事件未被处理。实测 `http_backend.py:532-534` **已处理**，收敛为终态 error。此项无需改动。

**D4（P2）进程内取消语义**

kagweb 的 `cancel()` 是**跨进程**的（`POST /v1/runs/{id}/stop`）。而 `AcpAgentLoopBackend` 的 docstring 提到「session-scoped backend keeps its connection alive across turns, so a mid-turn stop must reach the agent as a control message」。若用户的取消需在 Intellect 进程内也触发中止（而不只是 KAGWeb 侧停止消费），需确认 `/stop` 的语义是否足够——本次未核实。

##### C. 实际线上格式（Rust `/v1/runs/{run_id}/events` 实测）

> 上表列的是 `map_event_to_sse` 的**返回值**。实际落到 SSE 线上前还要经过 forwarder 一次包装（`api_server.rs:5129-5131`）：`None` 的返回值被改写为 `"message.delta"`。**这才是 BFF 真正收到的东西。**

每个 `data:` 行是 `RunEvent`（`run_state.rs:211-217`，`#[serde(flatten)] payload`）：

```json
{"event": <名>, "run_id": "...", "timestamp": 123.4, <payload 展平>}
```

| `event` | `type`（payload 内） | 其余字段 | 来源 |
|---|---|---|---|
| `message.delta` | `assistant.delta` | `text` | `map_event_to_sse` `None` 分支 → `:5131` |
| `message.delta` | `reasoning.delta` | `text` | 同上 |
| `message.delta` | `thinking.progress` | `elapsed_s`, `silent_s` | 同上（**心跳，无文本**） |
| `message.delta` | `assistant.completed` | `response`, `session_id` | 同上 |
| `message.delta` | `interim_assistant` | `content` | 同上 |
| `tool.progress` | `tool.started` | `tool_id`, `name`, `arguments` | `:2109` |
| `tool.progress` | `tool.completed` | `tool_id`, `name`, `result`, `duration_s` | `:2113` |
| `clarify` | `clarify` | `question`, `choices`, `clarify_id` | `:2129` |
| `error` | — | `message` | `:2117` |
| `run.started` | — | `session_id` | `:4979`（直接 `RunEvent::new`） |
| `approval.request` | — | **`tool_name`**, `arguments`, `choices` | `:5178` |
| `approval.responded` | — | `choice` | `:5206` |
| `run.completed` | — | `output`, `usage` | `:5297` |
| `run.failed` | — | `error` | `:5317` |
| `run.cancelled` | — | — | 终态补发（`run_state.rs:231` `is_terminal`） |
| `run.stopping` | — | — | `:5612` |

> **行号提示**：对齐文档（`intellect-team-alignment-requirements.md`）引用的 Rust 行号（1558/1564/4517 等）**已过时**——该文件写于 Rust 版本更早期。本文以上为 `9dcfdfa` 实测。行号变动本身也印证了对齐文档的判断：**Rust 是活跃演进的主版本**。

##### D. kagweb 与 Rust 的差异清单（Rust-only 对齐目标）

| kagweb 分支（`http_backend.py`） | 读取 | Rust 实际 | 差异 |
|---|---|---|---|
| 文本 | `event=message.delta`, **`delta`** | `event=message.delta`, `type=assistant.delta`, **`text`** | ❌ 字段名不符 → **内容全丢** |
| 推理 | `event=reasoning.available` | `event=message.delta`, `type=reasoning.delta` | ❌ 事件名不符 → **推理全丢** |
| 工具 | `event=tool.started` / `tool.completed` | `event=tool.progress` + `type=…` | ❌ 事件名不符 → **工具生命周期全丢** |
| 审批 | `event=approval.request`, `tool`, `preview` | `event=approval.request` ✅, **`tool_name`**, `arguments` | ⚠ 事件名对，字段错 → 工具名回落为 `"tool"`、预览为空 |
| 完成 | `event=run.completed`, `output`, `usage` | 同 | ✅ |
| 失败 | `event=run.failed`, `error` | 同 | ✅ |
| 取消 | `event=run.cancelled` | 同 | ✅ |
| 澄清 | **无分支** | `event=clarify`, `type=clarify`, `question`, `choices`, `clarify_id` | ❌ 未实现 |
| 心跳 | 无分支 | `event=message.delta`, `type=thinking.progress` | 可忽略（无文本） |

**结论**：在 Rust（权威）下，**文本、推理、工具、审批四项全部失效**——只有终态与失败可用。这不是边缘差异，是**主功能不可用**。

#### 落地建议

**Rust-only 对齐**（已决策：只对齐 Rust，Python 版待其成熟后逐步放弃）——需改 `_translate_run_event` 一个方法：

1. **判据改为 `type` 优先、`event` 兜底**：`kind = obj.get("type") or obj.get("event")`，再按语义分派。这样 `type=assistant.delta` 与 `event=run.completed` 都能命中。**不再做 Python 兼容**。
2. **文本增量读 `text`**（现读 `delta`）——`assistant.delta` 与 `reasoning.delta` 都用 `text`。
3. **工具分支改判 `type`**：`tool.started` / `tool.completed`（`event` 一律是 `tool.progress`），字段用 `name`/`result`/`duration_s`（现读 `tool`/`preview`）。
4. **审批字段改读 `tool_name`**（现读 `tool`），预览改读 `arguments`（现读 `preview`）。
5. **新增 `clarify` 分支**：映射为 `ask_user` 形状的卡片（复用 `_approval_question` 的构造方式），答复经 `respond_clarify` → `POST /v1/chat/completions/{session_id}/clarify`（body `{clarify_id, answer}`）。
6. **兜底与可观测性**：末尾 `return []` 前对未识别的 `type`/`event` 记 `log.debug`——当前静默丢弃使上述全部问题长期不可见。
7. **`intellect-team` 预设补齐** `turn_path="/v1/runs"` + `protocol="runs"`（见 D1）。

**遗留决策点**：Python 版仍会用旧格式发 `delta` 字段。Rust-only 后，若部署连的是 Python 版 intellect-team，文本将读不到。鉴于已决定逐步放弃 Python 版，这属于**可接受的有意破坏**，但应在 `builtin.py` 的 `intellect-team` 描述中注明「requires Rust api_server」。

> **落地时必须同步的两点**：(1) `intellect-team` 预设须补 `turn_path` / `protocol`（D1），否则改动无从生效；(2) 该预设的 `description` 应注明「requires Rust api_server」——Rust-only 后连 Python 版后端会出现**静默无输出**，描述里写清楚可省下一次排查。

1. **判据澄清**：把 `_translate_run_event` 的判据从 `event` 改为「`event or type`」——先按 `type` 分派，`event` 作为命名通道（`tool.progress` / `clarify` / `error`）的辅助。这是**同时兼容两个实现**的最小改动。
2. **字段兼容**：文本增量同时接受 `delta` 与 `text`。
3. **兜底与可观测性**：`_translate_run_event` 的末尾 `return []` 应至少 `log.debug` 未识别的事件类型——当前的静默丢弃使对接故障完全不可见（D 系列问题正因此长期未被发现）。
4. **修 `intellect-team` 预设**（D1）——这是唯一「当前必然失败」的一项。
5. **澄清 Python 版是否维护**（对齐文档的 P0 问题）——决定 kagweb 是否要做双版本兼容。

> **方法学教训**：本次问题的根因是**只对照了 kagweb 的代码，没有对照被对接方的代码**。前几轮评审中「契约完全匹配」的结论，是在只读 kagweb 侧、并假设 `intellect-team == intellect-runs` 的前提下得出的。跨仓库对接必须**两端都读**，且要确认**哪一端是权威**。

---

## 六、优先级建议

§四的改进项与 §五的产品决策对应关系，以及合并后的执行顺序。**「状态」列为 2026-09-10 实测**：

| 优先级 | 项 | 出处 | 理由 | 状态 |
|---|---|---|---|---|
| **P0** | **批次零：Intellect 对接修复（7 项）** | §五 决策 8 §D | **当前必然失败**：(a) 预设指向不存在的 `/agent/turn`；(b) Rust 下文本/推理/工具/审批**四项全失效**且无日志。不改则对接不成立 | ✅ 已落地（提交 `67d4759`；夹具同步换权威格式） |
| **P0** | 轮次门禁解耦（`required_service`） | §四 #1 | 决定多用户部署是否可行；决策 1 的前置 | ✅ 已落地（`d2ee506` + 修正 `cec68ff`；CLI/ACP 族另需显式授权） |
| **P0** | 移除死字段 / 死 UI（Skills、Tools 开关） | §五 决策 3 | 消除功能性误导，改动小、风险低 | ✅ UI/API 已移除（`5faeca0`、`0e25598`）；`kagweb/tools/` 本体留待决策 7 |
| **P1** | Agent Backend 提为顶级设置项 | §五 决策 2 | 纯前端导航调整，无后端风险 | ✅ 已落地（`21663be`） |
| **P1** | LLM 设置按 Intellect 门控 | §五 决策 5 | 与决策 2 同一次 UI 改动完成 | ✅ 已落地（`21663be`）；判据改为「自托管 HTTP 服务」，`intellect-runs` 一并覆盖 |
| **P1** | profile 增加 `model` + `context_window` | §四 #3/#4 | 用户核心诉求；一并修掉 P1-1 上下文错配 | ✅ 已落地（`model` 走 `{model}` 占位符/请求体；`context_window` 经 payload 键注入预算；未配置时行为不变） |
| **P1** | 人格子系统整体移出 | §五 决策 6 | 与决策 1 同源；**须早于 `.md` 打包规则移除** | ⬜ 未做 |
| **P1** | 合伙人与 IM 通道移除（约 42,000 行） | §五 决策 7 | 与决策 1 同源；**前置：迁移 `safe_filename`** | ⬜ 未做 |
| **P2** | 学习 / 研究表述清理 | §五 决策 4 | 学习者/监护人去留已决策为移除 | ✅ 完成（批次四/五：学习者子系统移除 + 导航更名 + 死词条清理，见 `ef8bbf5`） |
| **P2** | 工具层与 MCP 的重新定位 | §四 #6、§五 3c | 架构方向，取决于决策 1 的落地深度 | ⬜ 未做 |
| **P3** | 凭据打通（codex OAuth 复用） | §四 #10 | 需架构决策（涉及把凭据交给外部进程） |

**建议的执行批次**：

0. **批次零（对接修复，最高优先，Rust-only）**：决策 8 §D 的七项——
   - 0a. `intellect-team` 预设补 `turn_path="/v1/runs"` + `protocol="runs"`（**D1；不改则必然 404**）
   - 0b. `_translate_run_event` 判据改为 `type` 优先、`event` 兜底
   - 0c. 文本/推理改读 `text`；工具改判 `type` 并读 `name`/`result`/`duration_s`
   - 0d. 审批改读 `tool_name` / `arguments`
   - 0e. 新增 `clarify` 分支（复用 `ask_user` 卡片形状）
   - 0f. 末尾加未识别事件的 `log.debug` 兜底
   - 0g. `intellect-team` 的 `description` 注明「requires Rust api_server」

   **这是唯一「不改则 Intellect 对接不成立」的一组**，改动集中在 `builtin.py` 与 `http_backend.py` 两个文件。**已决策 Rust-only**，不做 Python 双版本兼容（Python 版待 Rust 成熟后逐步放弃）。
1. **批次一（低风险收敛）**：决策 3 的死字段/死 UI 清理 + 决策 2/5 的设置导航重构 + 决策 4 中的纯前端部分（导航文案与图标、死词条、孤儿 prompt hints、`presentation.tsx` 的陈旧目录）。这些互不依赖，都不触碰轮次主路径。
2. **批次二（解除阻断）**：§四 #1 门禁解耦 + 配套的 `grant` 增加 `agent_loop` 维度。这是让多用户 agent-backend 部署可用的关键一步。
3. **批次三（对接能力）**：§四 #3/#4 profile 增加 `model` 与 `context_window`，打通模型配置与上下文预算。可与批次四并行。**✅ 已完成**（`context_window` 的实际注入路径与本文初稿设想不同，受 executor 建 context 的时序约束，见 P1-1）。
4. **批次四（子系统移除，本体量最大）**：
   - 4a. **先迁移 `safe_filename`** 到 `kagweb/utils/`，验证 `attachment_store.py` 正常（**阻断性前置**）。
   - 4b. 决策 7 合伙人移除（约 42,000 行）。
   - 4c. 决策 6 人格移除 → 再删 `pyproject.toml` 的 `.md` 打包规则。
   - 4d. 顺带清理 `persona_context` 字段全链路、后台命令子协议、`GRANT` 的 `partners` 字段。
5. **批次五（方向性）**：决策 4 的学习者/监护人子系统移除已随批次五落地；工具层移除亦已落地，**MCP 重新定位（作为 MCP 服务端暴露工具）仍为后续方向**。

**决策 4 的两半**：

- **可随批次一执行**：导航文案与图标（`"Learning Agent"` / `"Learning Space"` / `"Learning"` 分组 + `GraduationCap` 图标，`nav-entries.ts:6,29,39`）、死词条清理、孤儿 prompt hints、`presentation.tsx` 的能力目录。这些都无后端依赖，改动廉价且**直接改变用户观感**。
- **已决策并落地**：默认 SOUL 模板、学习者档案 + 监护人子系统均已随批次四/五移除（存量 `preset: "learner"` 行加载时静默降级为 `standard`）。

**批次一可立即执行**——不涉及行为变更，只移除已被证明不可达的路径与误导性 UI。

**执行顺序上有三条硬约束**：

1. **决策 6 必须早于 `.md` 打包规则移除**（否则静默丢失内置人格，见决策 3a）。
2. **决策 7 必须先迁移 `safe_filename`**（否则 `attachment_store.py` 模块级导入失败，普通聊天附件上传中断，见决策 7）。
3. **决策 6 与决策 4 的 `locked_persona` 耦合**：`learner_grant()` 设 `locked_persona: "teacher"`，而 `teacher` 正是待移除的人格预设。移人格不等于移学习者子系统，但必须先决定后者的去留，否则会留下又一处指向不存在对象的学习者授权字段。

**决策 7 的净收益**：它使决策 6 的移除面**变小且更干净**——`persona_context` 字段的两个写入方（人格、SOUL）同时消失，字段本身可整体删除（见决策 6 第 1 项）。同时它把后端规模从 ~98,000 行降到 ~78,000 行，并让 `main.py` 的后台接线简化为只保留轮次恢复。

---

## 附：核验命令

```bash
cd /d/workspace/kagweb

# 主对话路径不依赖 LLM 层
grep -n "get_llm_config\|llm_selection" kagweb/capabilities/chat/capability.py   # 无输出

# 工具 schema 零调用者（P0-2）
grep -rn "get_tool_schemas(" --include=*.py kagweb/ | grep -v __pycache__

# AgentLoopRequest 无 model / tools 字段（仅第 4 行 docstring 命中）
grep -n "model\|tools" kagweb/services/agent_loop/protocol.py

# 门禁硬编码 llm（P0-1）
sed -n '105,134p' kagweb/services/session/turns/request_preparer.py

# profile schema 无 model（P1-3）
sed -n '1272,1310p' kagweb/services/config/runtime_settings.py

# 上下文窗口回落（P1-1）
grep -n "DEFAULT_CONTEXT_WINDOW_FALLBACK\|history_budget_ratio" \
  kagweb/services/llm/context_window.py kagweb/services/session/context_builder.py

# codex_auth 与 agent_loop 无关联（P1-4）
grep -rn "codex_auth" kagweb/services/agent_loop/    # 无输出

# 模型目录残留槽位（P2-2）
grep -n "def resolve_embedding_runtime_config\|def resolve_videogen_runtime_config" \
  kagweb/services/config/provider_runtime.py         # 无输出，但 SERVICE_NAMES 含二者
```

### 跨仓库核验（决策 8）

被对接方仓库不在本仓库内，需先克隆：

```bash
git clone --depth 1 https://gitee.com/wustbd/intellect-team.git /tmp/it-team

# R18：/agent/turn 端点不存在（kagweb 的 intellect-team 预设却指向它）
grep -rn "agent/turn" /tmp/it-team            # 无输出

# R19/R26：权威事件映射（Rust）
grep -n "fn map_event_to_sse" -A 40 /tmp/it-team/intellect-gateway/src/platform/api_server.rs
#   2100  TextDelta       → (None,              {"type":"assistant.delta","text":…})
#   2109  ToolCallStart   → (Some("tool.progress"), {"type":"tool.started",…})
#   2129  Clarify         → (Some("clarify"),      {"type":"clarify",…})

# R26：None 分支被改写为 message.delta —— 这才是线上 event 名的来源
sed -n '5129,5131p' /tmp/it-team/intellect-gateway/src/platform/api_server.rs

# run 级事件名（event 直接取自 RunEvent::new 的第一个参数）
grep -n 'RunEvent::new("' /tmp/it-team/intellect-gateway/src/platform/api_server.rs

# Rust 是否为权威（对齐文档自述）
sed -n '25,32p' /tmp/it-team/docs/agentui-alignment/intellect-team-alignment-requirements.md

# kagweb 侧的差异点
sed -n '460,535p' kagweb/services/agent_loop/http_backend.py   # _translate_run_event
sed -n '80,100p'  kagweb/services/agent_loop/builtin.py        # intellect-team 预设
```

### 基准漂移核验

```bash
# en/app.json 的键即英文原文，故只需 291 条与键不同的条目（e3eb12f）
python -c "import json;d=json.load(open('web/locales/en/app.json',encoding='utf-8'));print(len(d))"

# perf:check 读的是 .next-kagweb/，不会先构建——读数前必须重建
cd web && npm run build && npm run perf:check
```

## 评审说明

本文与前一份 [`backend-architecture.md`](./backend-architecture.md) 采用相同的核实标准：所有断言均以代码为准，引用带 `file:line`。

**与前文一致的地方**：`NoModelConfiguredError` / `LLMConfigError` 的分层处理**是正确的设计**，本文不建议改动——它在「无模型」与「模型坏掉」之间划清了界限，且 executor 的捕获范围经过刻意收窄（`executor.py:323-332` 的注释明确说明了这一点）。

**需要修正前文的地方**：`backend-architecture.md` §8 把「纯 agent-loop 部署只服务管理员」列为「待产品决策的已知边界，不是 bug」。本文的结论是：它确实是**已知**的，但在多用户场景下构成**功能性阻断**（P0-1），且与工具层不可达（P0-2）叠加后，非管理员的实际可用功能接近于零。建议将该条从「边界说明」提升为「待修复项」。

### 第二轮技术评审（2026-09-10，全文逐条复核）

§五加入 5 条产品决策后，对全文做了一次回归复核，发现并修正以下问题：

| # | 严重度 | 问题 | 修正 |
|---|---|---|---|
| R1 | **高** | **§五 3a 的清理建议有 bug**：原文建议删 `SKILL.md` 时「同步清 `pyproject.toml:259` 的 `"**/*.md"`」。实测 `kagweb/` 下被跟踪的 `.md` 共 **8 个**，除 5 个待删 SKILL.md 外，**另 3 个是 `persona/presets/*/PERSONA.md`**——`seed_presets()` 的数据源。移除该规则会让新装部署**静默丢失三个内置人格** | §五 3a 新增「打包陷阱」专栏，给出正确做法 |
| R2 | 中 | §五 3b 写 `services/search/` 为「4,812 行」——该数字实为 `services/parsing/` 的行数 | 更正为实测 **3,276 行** |
| R3 | 低 | §五 决策 4 称死词条「约 250 个键」——未经计数 | 补实测：两份 locale 各 **4,957** 键，`guidedLearning.*` **86** 个，键名含 learning **150** 个；250 改标为「量级估计」 |
| R4 | 低 | `nav-entries.ts` 行号区间 `:6,29-72` 过宽；`manager.py:1440,1491,1517` 未指向 `DEFAULT_SOUL_TEMPLATES` 定义处 | 收窄为 `:6,29,39`；soul 补 `:1410` 定义行 |
| R5 | 低 | `ASSESSMENT_SOURCES` 仅标注「3 个已移除」，未说明用途 | 补用途（持久化 `source` 白名单，`:178`）与「4 个全部对应已移除子系统」 |
| R6 | 低 | §P2-2 表格称 `search` 的消费方含 `tools.py`，而决策 3b 正要删除该文件 | 标注该消费方将随之移除 |
| R7 | 补充 | 决策 4 遗漏 `videogen` 导航项（「文生视频」，描述引用已移除的 chat videogen 工具） | 新增 #24b |
| R8 | 补充 | 决策 4「研究类 ≠ 残留」一节未说明 `deep_research` 的**可达性**；初稿曾标为待验证 | 本轮查清并补入结论（见下） |
| R9 | **高** | **决策 6 反转了三处早期建议**：(a) 决策 3a 的「打包陷阱」称 PERSONA.md「不能丢」，前提被推翻；(b) `skill-slug.ts` 的「改名保留」建议失效；(c) 决策 4 的 persona 预设从「待决策」变为「已决定移除」 | 三处均在原位置标注反转；决策 3a 的打包规则改写为「8 个 `.md` 全部删除后规则成为空规则」 |
| R10 | **高** | **决策 7 再次反转决策 6 的保留建议**：决策 6 称 `persona_context` 是双源字段「必须保留」，决策 7 移除合伙人后该字段两个写入方**全部消失** | 决策 6 第 1 项改写为「可整体删除」，并注明反转条件 |
| R11 | **高** | **调查结论与实测冲突**：合伙人调查断言「`CRON_RELOAD` 仍被 cron 子系统使用」，据此建议保留该枚举值。实测 `find kagweb -iname "*cron*"` **无任何结果**——cron 模块不存在，该值零消费者 | 决策 7 采信实测：4 个 `BackgroundCommandKind` 取值**全部可删**，并标注该冲突 |
| R12 | 补充 | 决策 7 发现一处**会打断核心服务**的模块级导入：`attachment_store.py:38`（普通聊天附件上传）依赖 `kagweb/partners/helpers.py:36` 的 `safe_filename` | 决策 7 把「迁移 `safe_filename`」列为**阻断性前置**，并写入执行顺序硬约束 #2 |

### 第三轮技术评审（2026-09-10，针对决策 6/7 的回归复核）

决策 6/7 是本文件最大的两次追加，对其实测数字与保留项做专项复核：

| # | 严重度 | 问题 | 修正 |
|---|---|---|---|
| R13 | **高** | **决策 7 的后端测试计数严重失真**：原文记「1,189 行」。根因是用了 `find -iname "*partner*"`——该模式**只匹配文件名**，漏掉整个 `tests/services/partners/` 目录（21 个文件、5,151 行）。按目录重算为 **29 文件 / 7,605 行**（差 6.4 倍） | 决策 7 规模表重算：总移除量从「约 35,400 行」改为「**约 42,000 行**」 |
| R14 | 低 | 前端计数用 `find -path "*partner*"`（**大小写敏感**），漏掉 1 个文件 | 改用 `-ipath`：49 → **50 个文件** |
| R15 | 低 | `components/partners/` 记为「25 个文件」 | 实测 **32 个**（18 顶层 + `group/` 14），已在正文注明构成 |
| R16 | 低 | `main.py` 的 `_handle_background_command` 记为「`:177-209`，56 行」 | 实测 `:179-210`，**32 行**；并补齐 `_start_partners`/`_stop_partners` 的精确区间 |
| R17 | 低 | `source_inventory.py` 记为「约 100 行」——实为提及数，非行数 | 改为「**50 处**提及」并列出所在区间 |

### 第四轮技术评审（2026-09-10，跨仓库对接核实）

起因是核实「`intellect-team` 是否支持审批」。为此克隆了被对接方仓库 `gitee.com/wustbd/intellect-team`（`9dcfdfa`）逐项比对，**推翻了本文此前多处结论**（详见 §五决策 8）。

| # | 严重度 | 问题 | 修正 |
|---|---|---|---|
| R18 | **致命** | **`intellect-team` 预设指向不存在的端点**。该仓库全仓库 `grep "agent/turn"` **无任何结果**，而 kagweb 的预设无 `turn_path`/`protocol` 字段，落到默认 `/agent/turn` → 部署必然 404。`intellect-runs` 已正确配置为 `/v1/runs` + `protocol="runs"`，企业版缺了同样的两行 | 决策 8 D1；列入批次零 |
| R19 | **高** | **`_translate_run_event` 对权威实现完全失效**。判据是 `obj["event"]`，而权威（Rust `map_event_to_sse`）除 `tool.progress`/`clarify`/`error` 外的**事件没有 SSE `event:` 行**——只有 `data: {"type": ...}`。*本轮（R26）进一步厘清*：forwarder 会把 `None` 分支改写为 `event="message.delta"`，故线上**有** `event` 但靠 `type` 区分种类；受影响面比本条初判更广（含工具与审批） | 决策 8 §C/§D；判据改为 `type` 优先 |
| R20 | **高** | **本文此前「契约完全匹配」的结论是错的**。第三轮评审只读了 kagweb 一侧，并假设 `intellect-team == intellect-runs`，据此判定两端对齐。**跨仓库对接必须两端都读，且确认哪端权威** | 决策 8 末尾「方法学教训」 |
| R21 | 中 | 本文称 `intellect-team` 走 turn 协议、「turn 不支持审批，是缺口」——定性错误。实为**服务端支持审批（`POST /v1/runs/{id}/approval`），kagweb 走错了通道** | 决策 8 D1 更正为「预设配置错误」 |
| R22 | 中 | 本文（第四轮中段）称「`run.cancelled` 未被处理」——**误**。实测 `http_backend.py:532-534` 已处理并收敛为终态 | 决策 8 D3 标注更正 |
| R23 | 中 | 本文（第四轮中段）称「`thinking.progress` 是思考内容、被静默丢弃」——**误**。它是**心跳脉冲**（只带 `elapsed_s`/`silent_s`，无文本）；真正携带推理文本的是 `message.delta` + `type: reasoning.delta` | 决策 8 表格已更正 |
| R24 | 补充 | `clarify` 与 `ask_user` 的关系此前未澄清。**意图相同、机制不同**：kagweb 已有把审批映射成 `ask_user` 卡片的机制（`capability.py:872-935`，注释即写「the agent-loop flavour of ask_user」），但 **`respond_clarify` 在 `RunsAgentLoopBackend` 中未实现**，且 Python 版 intellect-team 根本没有该 HTTP 端点 | 决策 8 D2 |
| R25 | 补充 | 发现一个**可观测性缺陷**：`_translate_run_event` 末尾 `return []`，未识别事件被静默忽略——R18/R19 这类对接故障因此**长期不产生任何诊断信号** | 决策 8 落地建议 #3 |
| R26 | **高** | **权威契约的层次此前未厘清**。`map_event_to_sse` 的返回值不是线上格式——forwarder 会把 `None` 分支改写为 `event="message.delta"`（`api_server.rs:5131`）。故 Rust 线上**有** `event` 字段，但文本/推理/工具全部靠 `type` 区分。据此重列差异清单：**文本、推理、工具、审批四项在 Rust 下全部失效**（不止文本与推理） | 决策 8 新增 `A/B/C/D` 四小节，给出权威契约与完整差异表 |
| R27 | 补充 | 对齐文档引用的 Rust 行号（1558/1564/4517）**已过时** | 以 `9dcfdfa` 实测行号替换，并注明该文档写于更早期 |
| R28 | 补充 | 审批字段亦不符：Rust 发 **`tool_name`**、`arguments`，kagweb 读 `tool`、`preview` → 工具名回落为通用 `"tool"`、预览为空 | 决策 8 差异表 D 行 + 落地建议 #4 |

**本轮方法论收获**：前三轮评审的可靠前提是「同一仓库内的代码可自证」；一旦涉及**跨仓库契约**，这个前提失效——必须克隆对端、确认权威实现、并逐事件比对字段。R18-R20 三个问题全部源于跳过这一步。

**本轮复核实为准确的项**（列出的保留判断，逐条验证）：

| 断言 | 验证方式 | 结果 |
|---|---|---|
| `BackgroundLeaderSupervisor` 零合伙人引用，需保留 | `grep -c -i partner` | **0** ✅ |
| `EventBus` 零合伙人引用，需保留 | `grep -c -i partner` | **0** ✅ |
| `orchestrator.py` 独立发布 `CAPABILITY_COMPLETE`（不依赖合伙人） | 发布者清单：`orchestrator.py:160` 与 `partners/manager.py:713` 各自独立 | ✅ |
| `CRON_RELOAD` 零消费者 | `find kagweb -iname "*cron*"` | **无结果** ✅ |
| 通道模块数 17 | `ls channels/*.py` 排除 4 个基础设施文件 | ✅ |
| `partner_access.py` 154 行 | `wc -l` | ✅ |
| `kagweb_cli/partner.py` 103 行 | `wc -l` | ✅ |
| `settings.py` 的 `is_partner_user_id` 分支（`:486-488`） | `grep -n` | ✅ |
| 端点 66 个（44 + 22） | 装饰器计数 | ✅ |
| 三目录 31/15/6 文件、12,749/5,165/2,540 行 | 按目录 `find` + `wc` | ✅ |

**方法学教训（R13 的根因）**：本项目多处统计使用 `find -iname` / `find -path`，两者分别只匹配**文件名**与**大小写敏感的路径**。凡声称「某子系统 N 行」时，必须按**目录**枚举后求和，不能依赖名字匹配——`tests/services/partners/` 这类「目录名含 partner、文件名不含」的情况会被完全漏掉。本文件此前的模块行数统计（§一 规模实测）采用的就是按目录枚举，故未受影响。

### 基准漂移复核（2026-09-10，提交时）

提交时远端（`gitee.com/wustbd/kagweb`）已领先 **20+ 个提交**，其中包含大量 `services/agent_loop/` 改动（新增 `acp_backend.py` 692 行、`http_backend.py` +295、`protocol.py` +45；`capabilities/chat/capability.py` ±217；`web/locales/en/app.json` −4,668 行）。本文基准 `c3ffe57` 因此**部分过时**，逐条复核结果如下。

**经复核仍然成立的断言**（本轮于新 HEAD 重新实测）：

| 断言 | 复核方式 | 结果 |
|---|---|---|
| `AgentLoopRequest` 无 `model` / `tools` 字段 | 字段全集 | 仍为 `prompt/history/session_id/language/workdir` ✅ |
| `ChatCapability` 对 LLM 层零依赖 | `grep -c get_llm_config\|llm_selection` | **0** ✅ |
| `get_tool_schemas()` 零调用者（P0-2 决定性证据） | 全仓库 grep | 仅定义处 ✅ |
| 轮次门禁仍硬编码 `has_capability_access("llm")`（P0-1） | `request_preparer.py` | ❌ **已不成立**：2026-09-10 起按 `required_service` 分派（§1.2） |
| 9 个 LLM 调用点 | 逐文件 grep | ✅（旁路调用点未变；主路径仍零依赖） |
| `SERVICE_NAMES` 仍含 `embedding`/`videogen` 残留（P2-2） | 计数 | ✅ 仍 8 个服务。**注**：`videogen` 有意保留——它有逐模型迁移默认值，删名会静默丢弃已存配置；仅其设置导航项已移除 |
| Skills 资产、人格预设、合伙人代码均未变 | `ls` + 文件计数 | ✅ 全部仍在（决策 3/6/7 尚未执行） |
| 子进程环境白名单（§1.4） | `acp_backend.py` 复用 `_build_child_env` | ✅ 新 ACP 族同样遵守 |

> **2026-09-10 补记**：上表成文于 `c3ffe57`。此后工作已落地 P0 批次零（Intellect 对接）、P0 门禁解耦、P1 设置导航与门控、以及工具/技能死面清理；`get_tool_schemas()`、`AgentLoopRequest` 无 model/tools、`ChatCapability` 零 LLM 依赖三条在这些改动后**仍成立**（均已复测）。

**需要修正的断言**（已在上文对应位置补记）：

1. **决策 5 的判定集合已不完整**——`intellect-runs`（Intellect 社区版，HTTP/runs 传输）未被 `AGENT_LOOP_INTELLECT_PRESETS` 覆盖。
2. **决策 5 的理由需重新审视**——`intellect` 已改为 CLI/ACP 族，「自托管 HTTP 服务因而需自带凭据」的原始理由对社区版不再直接成立。
3. **§1 的「两类后端族」表述仍成立但需细化**：family 仍只有 `cli` / `http`，但各新增了子变体——CLI 的 `transport`（`one-shot` | `acp`）、HTTP 的 `protocol`（`turn` | `runs`）。新增预设 `intellect-runs`。
4. **profile schema 新增两个字段**（`approval_timeout_seconds`、`approval_default`，`settings.py:306-311`），§P1-3 的「完整字段列表」需相应扩充；但**仍无 `model` 字段**，P1-3 的核心结论不变。
5. **检测能力已增强**：`/agent-loop/test` 现对 ACP 后端执行 `probe()` 握手探测（spawn → initialize → attach → shutdown），§1.6「只看在不在，不看能不能用」对 ACP 族已不再完全成立——**仍是「不发真实轮次」，但已比 `shutil.which` 深入**。

**尚未复核**：`capability.py` 的 ±217 行改动是否影响 §二 的调用点清单；`app.json` 的 −4,668 行是否已顺带清理了决策 4 所列的死词条。两者均需在新基准下重新盘点。

**[R8] `deep_research` 的实际可达性（本轮查清）**

结论：**不可达**。链路如下：

1. 前端能力目录是**硬编码**的（`web/features/capabilities/presentation.tsx:68` 的 `CHAT_CAPABILITIES`），把 `deep_research`（`:122`）、`deep_question`（`:115`）、`visualize` 等列为条目。
2. 但真正渲染的列表由 **后端**驱动：`mergeCapabilityPresentations()`（`:209`）只保留 `capability.available` 的项，再按 id 与硬编码目录做 `byId` 匹配。前端目录仅提供 label/icon/allowedTools 等**展示元数据**。
3. 后端 `GET /api/capabilities/registered`（`api/routers/capabilities.py:24-30`）返回的是 `get_capability_registry().get_manifests()`——而注册表只有 **`chat`** 一个（`BUILTIN_CAPABILITY_SPECS`）。

因此 `deep_research` 不会出现在能力列表中。它与 `course_study` / `mastery_path` / `immersive_reading` 同属前端遗留条目（后三者已被 `:168-179` 的过滤器显式排除）。

**更广的残留**：该文件的能力目录还引用了 `code_execution` / `imagegen` / `videogen`（`:28-48`）等工具与 `allowedTools` 列表（`:105-107`、`:122-124`），对应子系统均已移除。这属于 §五决策 4 与 §P0-2 的交叉地带——**前端工具/能力目录的整块陈旧数据**，建议在批次一中一并处理。

**复核实为准确、无需修正的项**：`request_preparer.py:105-134` 门禁代码；`capabilities/chat/capability.py:112-122` 无 LLM 依赖；`get_tool_schemas()` 零调用者（`orchestrator.py:179`）；`codex_auth` 与 `agent_loop` 无关联；9 个 LLM 调用点行号；`ChatRequestConfig(EmptyConfig)` 零字段；`AGENT_LOOP_INTELLECT_PRESETS` 值；`question_bank` 读路径孤立 / 写路径活跃。

**本文件所有精度声明**：行号与计数均为 2026-09-10 于 `c3ffe57` 实测；会随代码漂移的计数（locale 键数、代码行数）已在原处标注实测口径。
