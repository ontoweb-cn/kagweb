# KAGWeb 后端 LLM 部署机制分析与改进建议

> 适用版本：KAGWeb 0.2.2（`kagweb/__version__.py`）
> 核对基准：**tag `v0.2.2`**（2026-09-20 实测）。测量在版本号提交 `aad0eb1` 上完成，该提交到 tag 之间**只有文档改动、代码逐字节相同**，故以下数字与行号对 tag 同样成立。
> 关联文档：[`backend-architecture.md`](./backend-architecture.md)、[`kag-integration-design.md`](./kag-integration-design.md)、[`../ARCHITECTURE.md`](../ARCHITECTURE.md)

> **⚠ 阅读须知（2026-09-20）**：本文是**评审历史文档**，§一~§五的正文成文于 `c3ffe57`（2026-09-10），其后 KAGWeb 已推进 **150 个提交**（`c3ffe57..v0.2.2`），**多条结论已被落地改动推翻**。为免误导，凡已失效者均已在原位置加注；**当前状态的权威摘要见文末「§八 基准漂移总复核（2026-09-20，tag `v0.2.2`）」**——先读那一节再读正文。
>
> **文档结构**：§一~§四为**现状分析**（基于代码实测，`c3ffe57` 口径）；**§五为产品决策安排**（8 条方向性决策及其落地分析）；§六为优先级建议；§七为多用户身份映射；§八为最新基准的漂移复核。
> **基准说明**：§一~§四成文于 `c3ffe57`；§五决策 6-8 与「基准漂移复核」小节则在远端领先 20+ 提交后重新实测，Intellect 部分另经跨仓库核对（见决策 8）。
> **落地状态（2026-09-11，`ef8bbf5`）**：批次四/五已实施——合伙人+IM 通道、人格（决策 6，含决策 7 的 SOUL 模板与 `locked_persona` 耦合）、学习者+监护人子系统（决策 4）、内置工具包（决策 3a）均已移除，导航「Learning Agent/Learning Space/Learning」更名为 Chat/Space/Workspace。§五/§六中相应的「待决策」「批次五方向性」条目自此**已落地**；P2 状态见优先级表。
>
> **后续变更（2026-09-18）**：预设 `intellect-runs` 已并入 `intellect`，作为其 `transport="http"` 连接方式（`web/features/settings/sections/AgentLoopSettingsSection.tsx` 的同一张卡片内两个按钮）；`preset_family` / `per_turn_model_apply` / `llm_settings_apply` 均改为按 profile 的 transport 解析。本文中所有 `intellect-runs` 的表述在阅读时应对应「`intellect` 预设 + HTTP transport」，环境变量侧对应 `KAGWEB_AGENT_LOOP_TRANSPORT=http`。§五决策 5 提到的「判定集合不完整」问题随之消失：判据是 transport 的 family，而非预设名枚举。
>
> **后续变更（2026-09-20，`aad0eb1`）**：工具层与 MCP 客户端栈**整块移除**（P0-2 以「删除」而非「打通」结案）；`AgentLoopRequest` **已有 `model` 字段**（P1-2/P1-3 已修复，§1.1 的「无 model、无 tools」结论作废）；`NoModelConfiguredError` 文案已按要求改写（P2-3 关闭）；`AGENT_LOOP_INTELLECT_PRESETS` 枚举已删除，改为 `is_intellect_preset()` 前缀匹配 + `llm_settings_apply()` 按 transport 判定。

## 结论摘要

KAGWeb 剥离 agent loop 之后，**「LLM 部署」在代码里仍然指两件不同的事**，而两者的边界从未被重新划定：

| 概念 | 归谁管 | 现状 |
|---|---|---|
| **KAGWeb 自己的 LLM 层** | `services/llm/`（顶层 + `provider_core/` 共 9,233 行，`aad0eb1` 实测） | 仅服务 **8 个**旁路调用点；**不在主对话路径上** |
| **Agent backend 的模型** | 外部 CLI / HTTP 服务 | ~~KAGWeb **既不知道也不配置**——schema 里连 `model` 字段都没有~~ **已修正（2026-09-10 起）**：profile 有 `model` 与 `context_window`，`AgentLoopRequest.model` 逐回合下发 |

主对话路径（`ChatCapability`）**对 LLM 层零依赖**，已确认——该结论在 `aad0eb1` 复测仍成立（`grep -c "get_llm_config" kagweb/capabilities/chat/capability.py` → **0**；该文件对 `llm_selection` 的引用是修复 P1-2 后新加的模型名解析，见 §P1-2）。外围的四道耦合中，**四项已全部处理**：

1. ~~**非管理员用户无法使用任何 agent backend**~~ —— ✅ 已修复（`required_service` 门禁解耦，§1.2）。
2. ~~**KAGWeb 的工具层整体不可达**~~ —— ✅ 已结案：工具层**整块删除**（连同 MCP 客户端栈），不再有「可开关但不生效」的误导面。
3. ~~**上下文窗口错配**~~ —— ✅ 已修复（profile `context_window` 经 payload 键注入预算，§P1-1）。
4. ~~**模型选择器指向错误对象**~~ —— ✅ 已修复（`llm_selection` → `AgentLoopRequest.model`，并加 `model_selector_enabled` 门控，§P1-2）。

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
        │                        └──► 8 个旁路调用点（标题/洞察/摘要/诊断/搜索）
        │
        └──► 轮次门禁（has_capability_access("llm")）  ← 主路径阻断点
                                                              ⚠ 与 agent backend 无关

Agent backend 的模型
        │
        ▼
完全没有配置面：CLI 靠 ~/.claude、~/.codex 或 args 传 --model；
HTTP 靠服务端自己的配置
```

**关键事实**：`ChatCapability`（`capabilities/chat/capability.py:126-139`）只读 `agent_loop` 设置块并调用 `build_agent_loop_backend()`，**从不触碰 `get_llm_config()`**。~~`AgentLoopRequest`（`agent_loop/protocol.py:59-75`）的字段只有 `prompt / history / session_id / language / workdir`——**没有 `model`，也没有 `tools`**。~~

> **⚠ 上句已作废（2026-09-10 起，P1-3 修复）**：`AgentLoopRequest`（`agent_loop/protocol.py:72-94`）现在**有 `model` 字段**（`:94`，语义为「空 = 后端默认」），由 `llm_selection` 解析后逐回合下发；**仍然没有 `tools` 字段**，且这一条现在不会再改变——工具层已整块移除（P0-2 结案），KAGWeb 不再持有任何工具实现。字段全集为 `prompt / history / session_id / language / workdir / model`。

### 1.2 轮次门禁（资源授权）

`request_preparer.py`。门禁在 `start_turn` 中**同步**执行（跑完才 `create_task(_run_turn)`），且对**每个非管理员回合无条件生效**——包括调用方固定了 `llm_selection` 的情形。

```python
# request_preparer.py（节选，2026-09-10 修复后的结构；行号已按 aad0eb1 更新）
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

现位于 `request_preparer.py:186-196`（`_effective_required_service()` 定义在 `:42`）。

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

### 1.5 凭据双份：`codex_auth` 与 `codex` CLI —— ✅ 已以「退役」结案

> **⚠ 本节结论已被推翻（2026-09-19）**：`services/codex_auth/` 与 `openai_codex` provider **已整体退役**（提交 `ff761c7`，其评审记录含「no credential residue」的凭据残留核查与 38 对 `codex.oauth.*` 词条清理）。因此「凭据双份互不相通」不再是需要打通的缺口——**KAGWeb 侧那一份已不存在**，只剩 `codex` CLI 预设自己读 `~/.codex`。
>
> **残留处理（有意）**：`multi_user/model_access.py:54` 的注释说明 `openai_codex` 仍留在 `OWNER_BOUND_BINDINGS` 中——旧 catalog 文件里可能残留此类 profile，保留该绑定使其「永不可授予」，且未知 binding 会回落到默认 provider（已在退役评审中用 stale codex profile 探针验证）。
>
> 以下为成文时的分析：

| 维度 | KAGWeb 的 `openai_codex` provider | `codex` CLI agent-loop 预设 |
|---|---|---|
| 认证来源 | KAGWeb 自己的 OAuth 存储 `data/system/user-secrets/<owner>/private/openai-codex/` | Codex CLI 自己的 `~/.codex/auth.json` |
| 模型来源 | 登录后发布到模型目录的托管 profile | CLI 配置 / `args` / `env` |
| 消费 `codex_auth`？ | 是 —— **唯一**消费者 | **否** |

`services/agent_loop/` **不 import `codex_auth`**（`aad0eb1` 复测仍为 0 次）。

**运营后果**（成文时）：一份 ChatGPT/Codex 订阅不会在两者间共享。

**处置结果**：`docs/plans/2026-09-19-pending-decisions-credentials-and-history.md` 决策一提的三个选项（退役 / 维持 / CODEX_HOME 托管目录打通）最终选了**退役**。`codex` CLI 预设本身仍在（`builtin.py:136`），继续靠 CLI 自己的登录态工作；claude-code 预设同理，操作员经 profile env 填 `ANTHROPIC_AUTH_TOKEN` / `ANTHROPIC_BASE_URL`。

### 1.6 检测与测试：只看「在不在」，不看「能不能用」

`detect.py` 与 `/agent-loop/test` 端点（`api/routers/settings.py:1333`）都只做存在性检查：

- CLI：`shutil.which(command)`——**不执行子进程，不查版本，不验认证**。
- HTTP：2.5s 超时 GET——**任何 HTTP 响应（含 404）都算「可达」**。

**一个装了 `codex` 二进制但 `~/.codex/auth.json` 无效的部署，检测会报「available」。** 设置页的 Test 按钮同样不做认证检查。

> **✅ 部分改善（`aad0eb1` 复核）**：**ACP 族**已加握手探测——`AcpAgentLoopBackend.probe()`（`acp_backend.py:1001`）会真的 spawn → initialize → attach → shutdown，由 `/agent-loop/test` 调用（`settings.py:1477`）。one-shot CLI 族与 HTTP 族**仍维持原来的存在性检查**（HTTP 族只做 URL 形式校验，注释说明「turn 契约没有健康端点，POST 过去会真的跑一轮 agent」）。故本条对 ACP 族已不成立，对其余族仍成立。

### 1.7 环境变量契约

`.env.example` 只含宿主机端口与 TZ，**不含任何模型或后端凭据**。agent-loop 的环境变量覆盖是另一套（`runtime_settings.py:947-962` 的 `_apply_agent_loop_env_overrides`）：`KAGWEB_AGENT_LOOP_BACKEND` / `_COMMAND` / `_URL` / `_TRANSPORT` 与 `KAG_AGENT_LOOP_API_KEY`——**同样没有 model 键**（model 是每 profile 的常规配置，刻意不纳入环境覆盖，见 P1-3）。

---

## 二、真实 LLM 调用点清单

全部经由 `services/llm/factory.py` 的 `complete()` / `stream()`。**`aad0eb1` 实测为 8 处**，**无一在主对话答案路径上**。

| # | 位置 | 用途 | 路径性质 | 无 LLM 时的降级 |
|---|---|---|---|---|
| 1 | `session/turns/title_service.py:124` | 会话标题 | 轮次后后台 | 截断首条用户消息 |
| 2 | `session/turns/title_service.py:245` | 轮次洞察徽章 | 轮次后后台 | 静默跳过 |
| 3 | `session/context_builder.py:411` | **历史滚动摘要** | **轮次前，主路径** | 退化为截断（长会话丢连续性） |
| 4 | `services/search/consolidation.py:302` | 搜索结果合成 | 工具（**可选**） | 默认走模板，**不调用** |
| 5 | `api/routers/system.py:459` | 设置页「测试连接」 | 设置 UI | 报错 |
| 6 | `services/config/test_runner.py:219` | 诊断自检 | 诊断 | 报错 |
| 7 | `services/config/settings_spec.py:516` | 保存前探测 | 设置 UI | 报错 |
| 8 | `services/doctor.py:253` | `kagweb doctor --online` 的 provider 探测 | 诊断 | 报错 |

> #4 默认不触发：`web_search()` 仅在显式传入 `consolidation_llm_model` 时才走 LLM 合成（`search/__init__.py:240`），默认路径用 Jinja 模板。

**与原表（9 处）的差异**：原表第 4、5 项 `tools/brainstorm.py:89`、`tools/reason.py:104` **已随工具层整体移除**（P0-2 结案）；同时新增第 8 项 `services/doctor.py:253`——**它其实在 `c3ffe57` 时就已存在**（`af10330` 引入），原表遗漏了它。故口径为 9 − 2 + 1 = 8。

**确认无 LLM 调用的目录**：`services/parsing/`、`services/voice/`、`services/imagegen/`、`services/agent_loop/`、`multi_user/`（`aad0eb1` 复测仍成立；`multi_user/personal_models.py` 对 `services.llm` 的提及仅为 docstring 里的类型交叉引用，非调用）。

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

### P0-2 KAGWeb 工具层整体不可达 —— ✅ 已结案（2026-09-11 裁决：整块删除）

**结论**：工具层**不再存在**。`kagweb/tools/`、`api/routers/tools.py`、`runtime/providers/`、`ToolRegistry`/`ScopedToolRegistry`、`core/tool_protocol.py` 与 MCP 客户端栈（`services/mcp/`）均已移除；`get_tool_schemas()` 在 `aad0eb1` 全仓库零命中。「用户能开一个永远不生效的开关」这一**功能性误导**随之消失，因为开关本身也没了。

以下为成文时的分析，保留以记录判断依据：

**现象**：4 个内置工具（`brainstorm` / `web_search` / `paper_search` / `reason`）在 agent loop 部署下**永远不会被调用**，但：

- 在 `/api/tools` 中列为可用；
- 在设置页可开关（`enabled_optional_tools`）；
- 在授权体系里可授予（`grants.enabled_tools`、`multi_user/tool_access.py`）。

**根因**：工具的设计假设是「LLM 用 function calling 调用 KAGWeb 注册的工具」。这个假设随 in-process agent loop 一起被移除了。现在：

- `AgentLoopRequest` 没有 `tools` 字段——后端收不到工具 schema（**此条仍成立，且将永久成立**）；
- `ChatCapability` 不 import 也不使用 `ToolRegistry`；
- CLI 后端**只接收 prompt 字符串**，没有任何工具注入通道。

**决定性证据**：`ChatOrchestrator.get_tool_schemas()`（`orchestrator.py:179`）在全仓库**零调用者**。→ `aad0eb1` 复测：该**方法本身已删除**，`orchestrator.py` 从 200+ 行缩至 172 行。

**影响**：用户能开一个永远不生效的开关，管理员能授一个永远用不上的权限。这是**功能性误导**，比死代码更糟。 → **已通过删除消除**。

### P1-1 上下文窗口错配 —— ✅ 已修复

**现象**：agent loop 部署下历史预算被钉死在最低档。

**链路**：`executor.py` 把 `llm_config`（此处为 `None`）传给 `ContextBuilder.build()` → `_effective_context_window()` → `resolve_effective_context_window()` → 无模型名可匹配 → 回落 `DEFAULT_CONTEXT_WINDOW_FALLBACK = 16_384`（`llm/context_window.py`）→ 乘 `history_budget_ratio = 0.35`。

**结果：约 5,734 token 的历史预算**，喂给一个通常有 200K~1M 窗口的 Claude Code / Codex。

`LARGE_CONTEXT_MODEL_DEFAULT = 65_536` 的「大模型多给」兜底逻辑依赖**模型名匹配**（`KNOWN_LARGE_CONTEXT_MARKERS` 含 `claude`/`gpt-5` 等），而 agent loop 部署下模型名是空字符串——**永远走最低档**。

**影响**：长会话过早触发摘要甚至截断，丢掉本该保留的上下文。

> **备注（2026-09-20）**：P1-3 修复后 `AgentLoopRequest` 已有 `model` 字段，但该值**不回流**到 KAGWeb 的 `llm_config`——历史预算仍由 profile 的 `context_window` 决定，下面这条修复路径不变。

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

- ~~`ChatCapability` 完全不读它（grep 无命中）~~ **已作废**：修复后 `capability.py:741-750` 会读 `context.metadata["llm_selection"]` 并解析成具体模型名（见下方 ✅）；
- 它只影响 KAGWeb 自己的 LLM 层（标题、洞察、摘要）；
- 对 agent backend **零影响** → 已修复，见下。

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

### P1-4 凭据双份，互不相通 —— ✅ 已以「退役」结案

见 §1.5。运营者要维护两套 Codex 认证，且不理解为什么登录了 KAGWeb 的 Codex 之后 `codex` 预设仍然不可用。

> **✅ 2026-09-19 结案**：KAGWeb 侧那一份 `codex_auth` 已整体退役（提交 `ff761c7`），「要维护两套认证」的问题随之消失——现在只有一套（CLI 自己的 `~/.codex`）。本节描述的运营困惑不再存在。

### P2-1 检测不覆盖认证

见 §1.6。检测无法回答运营者真正关心的问题：「这个后端能用吗？」

### P2-2 模型目录残留服务槽位 —— ⚠️ 部分保留（有意）

`SERVICE_NAMES`（`model_catalog.py:151-160`）声明 8 个服务，但：

| 服务 | 解析函数 | 消费方 |
|---|---|---|
| `llm` | `resolve_llm_runtime_config` | 8 个旁路调用点 |
| `task` | 经 `service_name=TASK_SERVICE` 复用 | 标题生成（`model_selection/tasks.py`） |
| `search` | `resolve_search_runtime_config` | `system.py`、`test_runner.py`、搜索设置页（~~`tools.py:21`~~ 已随工具层移除） |
| `tts`/`stt` | 有 | `voice/` |
| `imagegen` | 有 | `imagegen/` |
| **`embedding`** | **不存在** | **无** |
| **`videogen`** | **不存在** | **无** |

`embedding` 与 `videogen` 对不上任何解析函数，是 RAG / videogen 剥离后的残留槽位，仍出现在设置界面。

> **`aad0eb1` 复核（本条结论部分失效）**：
> - **`videogen` 的导航项已移除**：`settings-nav.ts` 中已无 `videogen` 条目（初版 #24b 记录的问题已关闭）。但服务名**有意保留**——`runtime_settings.py:1269` 的复核注记说明：它带有逐模型的迁移默认值，**删名会静默丢弃已存配置**。
> - **`embedding` 完全无导航项**（`settings-nav.ts` 零命中），但仍留在 `SERVICE_NAMES` 与 `CONNECTABLE_SERVICES` 中，且 `model_catalog.py:177` 为它保留了连接路径映射（`/embeddings`）。属**仅存在于配置面的残留**，无 UI 入口，危害降为「死配置键」。
> - 因此本条从「用户可见的误导」降级为「配置面卫生问题」，优先级下调。

### P2-3 错误文案指向无效动作 —— ✅ 已修复

~~`llm/config.py:200-202` 的提示是 *"No active LLM model is configured. Please set it in Settings > Catalog."*~~

`aad0eb1` 实测（`llm/config.py:200-207`）文案已改写为区分场景的版本：

> *"No LLM model is configured. **Conversations are unaffected while an agent backend is set (Settings > Agent Backend)**, but KAGWeb's own calls — session titles, turn insights and history summaries — need one here: Settings > Models."*

它同时说明了「对话不受影响」与「哪些旁路功能需要模型」，与 §决策 5 的联动影响一节一致。

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
> **✅ 决策记录（2026-09-11）**：MCP 重定位采纳**撤退**——整块移除（`services/mcp/` ~3.4k 行、两个路由、Space MCP 页 + admin registry UI、`runtime/providers/`、`ToolRegistry`/`ScopedToolRegistry`/`deferred_tools`、`core/tool_protocol.py`、`mcp_tools` grant 维度、`mcp` 依赖与 brand-icons 管线）。理由：工具投递一环自 in-process loop 移除后即断裂（`build_tool_view` 零生产调用者），且无「用户自有 MCP server 参与对话」的真实需求；配置面虽活跃但不支撑任何端到端价值。附件可达性缺口已按「附件落盘 session workspace + manifest 带路径」落地（同日，`attachment_workspace.py`）。
>
> **✅ 后续收口（2026-09-11，人工 E2E）**：① 附件链路人工端到端验证（CLI backend 对话上传附件 → manifest 路径 → agent 实读 → 删除清理）首次走通——此前物化门控读取 profile 上不存在的 `family` 键，**真实环境从未触发**（`claude-code` 预设下 agent 拿不到文件）；family 现经 `agent_loop.settings.profile_family()` 从 preset 注册表派生，随 preparer 单次 settings 读取放 payload 下发（`agent_loop_profile_family`），executor 兜底探测仅作 fallback。② 会话删除现同步回收 workspace：附件副本、按 turn id 键控的 `events.jsonl` 目录、空会话目录（agent 自写产物保留）；附件 store 原件清理收敛进 `workspace_cleanup.purge_session_artifacts`（API 路由与 SDK facade 共用）。③ CLI `run` 的 `--tool` 选项喂给已删除的契约字段导致 `kagweb run chat` 必然 pydantic 报错，已随工具包移除一并摘除。

**7. 清理模型目录残留**（P2-2）

`SERVICE_NAMES` 移除 `embedding` / `videogen`，或在设置界面标注为「未实现」。

**8. 修正错误文案**（P2-3）

`NoModelConfiguredError` 的提示应区分场景：agent loop 部署下指向 Settings → Chat → Agent Loop。

**9. 检测增强**（P2-1）

CLI 检测可考虑增加**可选**的 `--version` 探测（当前刻意不做，是为了「side-effect free」）；更有价值的是在 profile 上增加一个「认证自检」按钮，明确告知运营者后端是否真的可用。

**10. 凭据打通（可选）**（P1-4）—— ❌ **已作废（2026-09-19）**

原建议：让 `codex` agent-loop 预设复用 `codex_auth` 的 token（CLI 后端在子进程 env 中注入从 `codex_auth` 取得的凭据，而非让 CLI 读 `~/.codex`）。

**该建议连同其前提一起消失**：`codex_auth` 已整体退役，没有 token 可注入了。退役而非打通的方向选择，正好也避开了本建议自己提示的风险——「把 KAGWeb 管理的凭据交给外部进程，应视为架构决策而非实现细节」。

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
| `services/llm/`（顶层 4,842 + `provider_core/` 4,637 行） | **大幅缩减**，非全删 | 8 个旁路调用点中仅少数仍需（见决策 5） |
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

### 决策 3：移除 Skills 与 Tools —— ✅ 已全部落地

> **最终结果（`aad0eb1`）**：Skills 与 Tools **都已移除**，且 3b 中列为「必须保留（MCP 底座）」的那批文件也**一并移除了**——因为 MCP 客户端栈随后被裁决为整块撤退（见 3c）。本节保留的是成文时的移除边界分析，**「必须保留」清单已全部作废**，请以本节末尾的现状小结为准。
>
> **现状小结**：`kagweb/tools/`、`kagweb/skills/`、`api/routers/tools.py`、`core/tool_protocol.py`、`runtime/providers/`、`services/mcp/`、`ToolRegistry`/`ScopedToolRegistry`/`deferred_tools`、`runtime/registry/tool_registry.py` 均**零残留**；`pyproject.toml` 的唯一 `.md` 规则是 `kagweb_cli = ["**/*.md"]`（指向 `kagweb_cli/README.md`，与 Skills/人格无关）。

两者（当时）的移除成本**差异很大**，因为 MCP 以工具协议为底座。

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
> **✅ 已裁决（2026-09-11）**：不重新设计，整块移除——见 §四 #6 的决策记录。

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
| 25 | `api/routers/auth.py:402` `require_signed_in`（原名 `require_learning_surface`） | 它是**通用鉴权门**——`api/main.py:425-428` 的 `_auth` 依赖，施加于**全部受保护路由**。其自身 docstring 已说明「学习界面随能力层移除，该依赖保留为唯一鉴权接缝」。**改名（2026-09-11 已改为 `require_signed_in`），不要移除** |
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

### 决策 5：仅 Intellect 社区版/企业版启用 LLM 设置 —— ✅ 已落地（判据已重设计）

> **落地结果（`aad0eb1`）**：门控已实现，但**判据不是本文设计的「预设名集合」，而是「自托管 HTTP 服务」**——`llm_settings_apply()`（`agent_loop/builtin.py:381`）在解析出 transport 后判定 `is_intellect_preset(preset) and resolved.family == "http"`。同时门控**收窄到「对话用 LLM」这一个叶子**（特意不隐藏整个 models 分类，因为 `task` 服务还要喂 KAGWeb 自己的标题/洞察/摘要调用，全隐藏会把唯一能修那条告警的页面也藏掉）。
>
> **`AGENT_LOOP_INTELLECT_PRESETS` 枚举已删除**——`runtime_settings.py:103-105` 的注释明确记录删除理由：「第二真源，且已经漂移（它从不知道 `intellect-runs`）」。故下文的「集合不完整」问题**以删除枚举的方式解决**；`intellect-runs` 也已并入 `intellect`（见文首 2026-09-18 注）。前端拿不到 primary preset 的问题同样已解决：`_agent_loop_payload()` 现返回 `effective_primary.llm_settings_enabled`（`settings.py:1269`），前端由 `SettingsAccessProvider.tsx:30` 消费。
>
> 以下为成文时的分析，保留以记录推导过程。

**规则**（原始表述）：当且仅当 primary agent backend 为 `intellect` 或 `intellect-team` 时，启用 Settings 中的 LLM（模型与连接）分区；其余后端一律不呈现 LLM 设置。

**判定基元已有**：~~`AGENT_LOOP_INTELLECT_PRESETS = frozenset({"intellect", "intellect-team"})`（`runtime_settings.py:97`）~~ **该枚举已删除**。

> **⚠ 该集合已不完整（2026-09-10 补记）**。远端在此期间合入了大量 agent-loop 变更，Intellect 现在有**三个**预设，而集合只含两个：
>
> | 预设 | family | 传输 | 是否在集合内 |
> |---|---|---|---|
> | `intellect` | **cli** | ACP（`intellect acp`，长驻子进程） | ✅ |
> | `intellect-team` | http | runs（`/v1/runs` + SSE；D1 已修复，预设自带该 transport 默认值） | ✅ |
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
       "llm_settings_enabled": <bool>,   # 落地后实现为 profile_llm_settings_apply(resolved)
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

- 关闭 LLM 设置后，8 个旁路调用点中的**设置类**（`system.py:459` 测试连接、`settings_spec.py:516` 保存探测）失去入口——**方向正确**。
- 但**后台类**调用点仍会执行：会话标题（`title_service.py:124`）、轮次洞察（`:245`）、**历史摘要**（`context_builder.py:411`）。若不配模型，这三者按 §二的降级列正常退化（摘要退化为截断）。

> **✅ 该后果已在 `NoModelConfiguredError` 文案中说明**（`llm/config.py:200-207`，2026-09-20 复核）：提示明确写出「Conversations are unaffected while an agent backend is set (Settings > Agent Backend), but KAGWeb's own calls — session titles, turn insights and history summaries — need one here: Settings > Models.」——即本文建议的「在设置页说明这一后果」以错误文案的形式落地了。剩余未做的是「对非 Intellect 后端默认关闭轮次洞察」。

### 决策 6：人格（Persona）整体移出 KAGWeb —— ✅ 已落地

> **落地结果（`aad0eb1`）**：`services/persona/`、`api/routers/personas.py`、`web/lib/skill-slug.ts`、`web/components/space/PersonasSection.tsx` 均已删除；`git ls-files 'kagweb/**/*.md'` 为 **0**；`executor.py` 的人格加载分支与 `context.persona_context` 字段同时消失（后者因决策 6+7 的两个写入方都移除而可整体删除，见下）。**`.md` 打包规则的最终处置**：`pyproject.toml` 中 `"**/*.md"` 已改为只覆盖 `kagweb_cli`（`kagweb_cli = ["**/*.md"]`），指向 `kagweb_cli/README.md`，与 Skills/人格无关。

**规则**：人格**不在 KAGWeb 内配置**，一律从 agent backend 获取。

**现状**（成文时）：KAGWeb 持有一整套 in-process 人格子系统，与决策 1（作门面）直接冲突。

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


### 决策 7：移除合伙人（Partners）与 IM 通道 —— ✅ 已落地

> **落地结果（`aad0eb1`）**：`kagweb/partners/`、`kagweb/services/partners/`、`kagweb/services/partner_groups/`、`api/routers/partners.py`、`api/routers/partner_groups.py`、前端 `components/partners/**` 与 5 个路由页、`kagweb_cli/partner.py` 均已删除（`git ls-files` 全为 0）。`safe_filename` 的阻断性迁移前置已完成——它现位于 `kagweb/utils/filenames.py:16`，`services/storage/attachment_store.py` 的模块级导入已改指该处。
>
> **⚠ 后台命令子协议未完全清除**（本文成文时判断「4 个取值全部可删」，实际只删了 3 个）：`BackgroundCommandKind` 仍在（`coordination/types.py:37-38`），只剩 `CRON_RELOAD` 一个取值且零消费者；`submit/read/acknowledge_background_command` 三方法也仍在（`memory.py:167-200`、`redis.py:413` 起），`submit` 在生产代码中零调用者。**该残留已记入 `backend-architecture.md` §9 第 5 项。**

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

**D5（P0）通道选择错误会静默产出空回合 —— ✅ 已修复（2026-09-15）**

「用 Intellect 的 `/v1/chat/completions` 当 agent loop」在当前版本下**不成立**，且失败方式是静默的：该端点返回 200 并正常流式，所以从外面看一切健康。实测把它的真实形状喂给 KAGWeb 的解析器：

```
{"choices":[{"delta":{"content":"Hello"}}]}         -> DROPPED
{"choices":[{"delta":{"role":"assistant"}}]}        -> DROPPED
{"tool":"…","label":"…","status":"running"}         -> DROPPED
```

两条原因叠加：(a) `http_backend` 不认识 OpenAI 的 `choices[].delta` 包裹（`choices` 只用于解析审批选项）；(b) SSE 解析器**只读 `data:` 行、丢弃 `event:` 名**，于是 `event: intellect.tool.progress` 的类型标识也没了。

该端点在协议层确实给不了 agent 语义：**无推理通道**（`api_server/adapter.py` 全程未注册 `reasoning_callback`）、**工具只有 `running`/`completed` 标记**（payload 无 args、无 result）、**无审批/澄清/停止**（三者都挂在 `run` 资源下，因为只有 run 是有身份、可寻址、可恢复的执行单元）。这是契约错位，不是缺陷：OpenAI chat-completions 是无状态文本契约。

修复（三处，均带测试）：

1. `_is_openai_chat_payload` 按 payload 形状（`object` 标记，或 `choices[].delta|message`）识别并**立即报错**，文案直接指向 `/v1/runs` 与 `intellect-runs` 预设。按形状而非某个字段名匹配，漏掉标记的厂商同样被捕获。
2. 整条流**一帧都读不出来**时 fail-fast（`agent_loop.stream_not_understood`），不再变成「空答案」——后者会让运维去查模型，而真正错的是端点。判据用**未映射帧数**而非"没产出事件"：已识别但合理地不产出内容的帧（心跳、生命周期标记）不算错。注意轮询产出的帧**不计入**，否则轮询会掩盖失败。
3. SSE 解析器保留 `event:` 名并作为**兜底**塞进 payload（仅当 payload 自己没有 `type`/`event`），让「类型只在 `event:` 行上」的厂商也能映射。

**D6（P0）`clarify` 事件名与端点双双错配 —— ✅ 已修复（2026-09-15）**

D2 记录时以为只是「未实现」，实测是**三处错配**（社区版 Python `api_server`）：

| 项 | kagweb 原实现 | Intellect 实际 |
|---|---|---|
| 事件名 | `kind == "clarify"` | `event: "clarify.request"`（与 `approval.request` 同族） |
| 端点 | `POST /v1/chat/completions/{session_id}/clarify` | `POST /v1/runs/{run_id}/clarify` |
| body 字段 | `{clarify_id, answer}` | `{response, clarify_id}` |

后果：澄清卡**永远不会出现**（事件名不匹配，`_translate_run_event` 落到 unknown 分支）；即使出现了，答复也会 404（端点只存在于另一个构建）；即便端点在，字段名也会被判 `missing 'response' field`。

修复：事件名接受 `clarify.request`（保留裸 `clarify` 兼容其它适配器）、端点改为 run-scoped（与审批同源，随 run API 一起可用）、body 改用 `response`。附带收益：`_session_id` / `_remember_session` 这两个只为旧端点而存在的字段成为死状态，一并移除。

**D7（P2）文档与预设的纠偏 —— ✅ 已落地（2026-09-15）**

- `custom-http` 预设描述原文「Any service speaking the documented streaming turn contract」易被读成 OpenAI 客户端。改为明确写出「这是 KAGWeb 自己的中性帧契约，**不是** OpenAI chat-completions 客户端，指向 `/v1/chat/completions` 会失败；应指向服务的 agent 端点（Intellect 即 `/v1/runs`）」。
- 本文 D2 的「`RunsAgentLoopBackend` 未实现 `respond_clarify`」与「Python 版根本没有这个 HTTP 端点」两句**已过时**，见 D6 更正。§941 行的实现建议同理作废。

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

> **⚠️ 本节已全部作废（2026-09-15 实测）**：下表的差异是成文时的状态，此后已逐项修复。现按**权威 Rust 形状**逐条复测（`_translate_run_event` 已改为 `type` 优先分派）：

| 语义 | 现状 | 说明 |
|---|---|---|
| 文本 | ✅ 通过 | `type=assistant.delta` 进缓冲，在 `assistant.completed`/`run.completed`/工具边界 flush 成 content（复测：两段 delta → `content="Hello world"`） |
| 推理 | ✅ 通过 | `type=reasoning.delta` → `thinking` |
| 工具 | ✅ 通过 | `event=tool.progress` + `type=tool.started` → `tool_call`（**args 保留**）；`type=tool.completed` → `tool_result`（result 保留） |
| 审批 | ✅ 通过 | `tool_name` / `arguments` 均已读取 |
| 澄清 | ✅ 通过 | `clarify.request` → `clarify_request`；见 D6 |
| 完成/失败/取消 | ✅ 通过 | 无变化 |
| 心跳 | ✅ 通过 | `thinking.progress` 显式识别为 no-op |

保留原表以便对照当初的判断：

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

**当初的结论**：在 Rust（权威）下，**文本、推理、工具、审批四项全部失效**——只有终态与失败可用。这不是边缘差异，是**主功能不可用**。（现已修复，见上表。）

##### E. 通道能力矩阵（选型必读）

`agent_loop` profile 能做什么，取决于它连的是**哪一类端点**，而不是哪个服务。同一台 Intellect 上，三条通道的能力差一个数量级：

| 能力 | ACP（`intellect` CLI 预设） | runs（`intellect-runs`，`/v1/runs`） | chat-completions（`/v1/chat/completions`） |
|---|---|---|---|
| 文本 | ✅ content 块 | ✅ assistant.delta | ✅ `choices[].delta.content` |
| 推理 | ✅ `thinking` | ✅ `reasoning.delta` | ❌ **不存在**（未注册 reasoning 回调） |
| 工具参数 | ✅ `raw_input` | ✅ `arguments` | ❌ 只有 `running` 标记 |
| 工具结果 | ✅ content 块 | ✅ `result` | ❌ 只有 `completed` 标记 |
| 审批 | ✅ `request_permission` | ✅ `approval.request` | ❌ **无** |
| 澄清 | ❌ 未暴露（`respond_clarify` 抛 NotImplementedError） | ✅ `clarify.request` + `/v1/runs/{id}/clarify` | ❌ **无** |
| 停止 | ✅ ACP `cancel` | ✅ `/v1/runs/{id}/stop` | ❌ **无** |
| 截断/未完成信号 | ✅ `stop_reason` | ✅ `run.failed` / `status` | ⚠️ 仅在完整响应体（`intellect.completed`）与响应头，流式期间不可得 |
| 多轮会话状态 | ✅ agent 侧 session id（可 `load_session` 续接） | ✅ `session_id` | ⚠️ 无状态语义 |
| KAGWeb 侧 | `AcpAgentLoopBackend` | `RunsAgentLoopBackend` | **不支持——会 fail-fast 报错**（D5） |

**选型规则**：agent 语义只存在于**有身份的执行单元**上——ACP 的 session 与 runs 的 run。chat-completions 是 OpenAI 的**无状态文本**契约，一问一答即止：它结构上放不下「暂停→等答复→恢复」，也放不下带身份的推理与工具。需要 agent 行为就用 ACP 或 runs；只需要文本补全才用 chat-completions，且不要把它配成 agent loop。

##### F. 行为变更记录（升级注意事项）

**`stream_not_understood`：读不懂的流从「空答案」改为「回合失败」**（2026-09-15）

HTTP 族的通用 «turn» 协议（`custom-http`）现在会 **fail 掉回合**，条件是：整条流里
**至少有一帧完全无法映射，且一帧都没成功映射出来**。此前这种情况会静默产出空答案，
运维只能看到「空回复」并把注意力引向模型，而真正错的是端点。

**对既有部署的影响**：只指向能正常解析的端点的部署**不受影响**（已识别的帧即使合理地不产出
内容——心跳、生命周期标记——也不触发）。命中的是「端点选错/协议不符」这类**本来就没在工作**
的配置：它从「静默空答案」变成「明确报错」，正是本次改动的目的。轮询产出的帧**不计入**成功
映射，避免降级路径掩盖失败。

**OpenAI chat-completions 端点**：新增按 payload 形状识别（`object` 标记或
`choices[].delta|message`）并**立即报错**，文案指向 `/v1/runs` 与 `intellect-runs` 预设——
见 D5。同样属于「本来就不成立」的配置。

#### 落地建议

**Rust-only 对齐**（已决策：只对齐 Rust，Python 版待其成熟后逐步放弃）——需改 `_translate_run_event` 一个方法：

1. **判据改为 `type` 优先、`event` 兜底**：`kind = obj.get("type") or obj.get("event")`，再按语义分派。这样 `type=assistant.delta` 与 `event=run.completed` 都能命中。**不再做 Python 兼容**。
2. **文本增量读 `text`**（现读 `delta`）——`assistant.delta` 与 `reasoning.delta` 都用 `text`。
3. **工具分支改判 `type`**：`tool.started` / `tool.completed`（`event` 一律是 `tool.progress`），字段用 `name`/`result`/`duration_s`（现读 `tool`/`preview`）。
4. **审批字段改读 `tool_name`**（现读 `tool`），预览改读 `arguments`（现读 `preview`）。
5. **新增 `clarify` 分支**：映射为 `ask_user` 形状的卡片（复用 `_approval_question` 的构造方式），答复经 `respond_clarify` → `POST /v1/chat/completions/{session_id}/clarify`（body `{clarify_id, answer}`）。
   > **⚠️ 已作废（2026-09-15）**：端点与 body 字段都不对，见 D6——实际是 `POST /v1/runs/{run_id}/clarify`（body `{response, clarify_id}`），事件名是 `clarify.request`。该条已按 D6 实现。
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
| **P0** | 移除死字段 / 死 UI（Skills、Tools 开关） | §五 决策 3 | 消除功能性误导，改动小、风险低 | ✅ 已落地（`5faeca0`、`0e25598`）；`kagweb/tools/` 本体亦已移除（决策 7 批次） |
| **P1** | Agent Backend 提为顶级设置项 | §五 决策 2 | 纯前端导航调整，无后端风险 | ✅ 已落地（`21663be`） |
| **P1** | LLM 设置按 Intellect 门控 | §五 决策 5 | 与决策 2 同一次 UI 改动完成 | ✅ 已落地（`21663be`）；判据改为「自托管 HTTP 服务」，`intellect-runs` 一并覆盖 |
| **P1** | profile 增加 `model` + `context_window` | §四 #3/#4 | 用户核心诉求；一并修掉 P1-1 上下文错配 | ✅ 已落地（`model` 走 `{model}` 占位符/请求体；`context_window` 经 payload 键注入预算；未配置时行为不变） |
| **P1** | 人格子系统整体移出 | §五 决策 6 | 与决策 1 同源；**须早于 `.md` 打包规则移除** | ✅ 已落地（`services/persona/` 与 3 个 PERSONA.md 均已删除；`.md` 规则收窄为 `kagweb_cli`） |
| **P1** | 合伙人与 IM 通道移除（约 42,000 行） | §五 决策 7 | 与决策 1 同源；**前置：迁移 `safe_filename`** | ✅ 已落地（`kagweb/partners/`、`services/partners/`、`services/partner_groups/` 零残留） |
| **P2** | 学习 / 研究表述清理 | §五 决策 4 | 学习者/监护人去留已决策为移除 | ✅ 完成（批次四/五：学习者子系统移除 + 导航更名 + 死词条清理，见 `ef8bbf5`） |
| **P2** | 工具层与 MCP 的重新定位 | §四 #6、§五 3c | 架构方向，取决于决策 1 的落地深度 | ✅ 2026-09-11 裁决：撤退，整块移除 |
| **P2** | 模型选择器指向错误对象 | §三 P1-2 | 用户选的模型对后端无效且无提示 | ✅ 已修复（`llm_selection` → `AgentLoopRequest.model` + `model_selector_enabled` 门控） |
| **P2** | 错误文案指向无效动作 | §三 P2-3 | 提示把用户引向 Catalog，而配置在 Agent Backend | ✅ 已修复（文案区分「对话不受影响」与「旁路调用需要模型」） |
| **P2** | 模型目录残留服务槽位（`embedding`/`videogen`） | §三 P2-2 | 无解析函数的服务名 | ⚠️ **部分保留**：`videogen` 有意保留（删名会丢已存配置）、导航项已移除；`embedding` 仅存于配置面（见 §三 P2-2） |
| **P3** | ~~凭据打通（codex OAuth 复用）~~ | §四 #10 | ~~需架构决策~~ | ✅ **以退役结案**（2026-09-19，`ff761c7`）：`codex_auth` 与 `openai_codex` provider 已删除，无 token 可打通 |
| **P3** | 检测增强（认证自检） | §三 P2-1 | CLI 探测刻意不做 `--version` | ⚠️ 部分：ACP 族已加 `probe()` 握手探测（`acp_backend.py:1001`），CLI/HTTP 族仍只看「在不在」 |

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
5. **批次五（方向性）**：决策 4 的学习者/监护人子系统移除已随批次五落地；工具层移除亦已落地；**MCP 重新定位已于 2026-09-11 裁决为撤退（整块移除，见 §四 #6 决策记录）**。

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

> **⚠ 本节命令的基准是 `c3ffe57`**。以下命令中有多条已因代码变更而**不再可用**（目标文件/行为已改），已在原处标注；末尾给出 `aad0eb1` 的替代命令。

```bash
cd /d/workspace/kagweb

# 主对话路径不依赖 LLM 层（✅ 仍成立）
grep -n "get_llm_config" kagweb/capabilities/chat/capability.py   # 无输出
# 注意：不要用 "get_llm_config\|llm_selection" —— 后者现在有命中（P1-2 修复新增）

# 工具 schema 零调用者（P0-2）—— ❌ 已失效：方法连同工具层一起删除了
grep -rn "get_tool_schemas(" --include=*.py kagweb/ | grep -v __pycache__   # 无输出（定义处也没了）

# AgentLoopRequest 无 model / tools 字段（P0-2）—— ❌ 已失效：现在有 model 了
grep -n "model\|tools" kagweb/services/agent_loop/protocol.py   # 有 model 命中

# 门禁硬编码 llm（P0-1）—— ❌ 已失效：门禁已解耦
sed -n '186,196p' kagweb/services/session/turns/request_preparer.py

# profile schema 无 model（P1-3）—— ❌ 已失效：profile 现有 model / context_window
sed -n '1272,1310p' kagweb/services/config/runtime_settings.py

# 上下文窗口回落（P1-1，✅ 常量仍在）
grep -n "DEFAULT_CONTEXT_WINDOW_FALLBACK\|history_budget_ratio" \
  kagweb/services/llm/context_window.py kagweb/services/session/context_builder.py

# codex_auth 与 agent_loop 无关联（P1-4）—— ⚠️ 现为平凡成立：该模块已整体退役
git ls-files kagweb/services/codex_auth/ | wc -l    # 0
grep -rn "codex_auth" kagweb/services/agent_loop/   # 无输出

# 模型目录残留槽位（P2-2，⚠️ 仍成立但已降级）
grep -n "def resolve_embedding_runtime_config\|def resolve_videogen_runtime_config" \
  kagweb/services/config/provider_runtime.py         # 无输出，但 SERVICE_NAMES 含二者
```

### tag `v0.2.2` 的替代核验命令（实测于 `aad0eb1`）

```bash
# 已移除子系统的零残留断言（全部应为 0）
for d in partners tools services/partners services/persona services/mcp runtime/providers; do
  echo "$d: $(git ls-files kagweb/$d | wc -l)"; done

# LLM 旁路调用点（应为 8 处，见 §二）
grep -rnE "llm_stream\(|llm_complete\(|llm\.complete_sync\(|await complete\(" \
  --include="*.py" kagweb/ | grep -v __pycache__ | grep -v "^kagweb/services/llm/"

# AgentLoopRequest 字段全集（现有 model，仍无 tools）
grep -n "class AgentLoopRequest" -A 22 kagweb/services/agent_loop/protocol.py

# LLM 设置门控改为按 transport 判定（决策 5 的落点）
grep -n "def llm_settings_apply" -A 25 kagweb/services/agent_loop/builtin.py

# Intellect 预设与前缀匹配（原 AGENT_LOOP_INTELLECT_PRESETS 已删除）
grep -n "def is_intellect_preset" -A 8 kagweb/services/agent_loop/builtin.py

# codex 凭据链路零残留（2026-09-19 退役）
git ls-files kagweb/services/codex_auth/ | wc -l                    # 0
grep -rl "openai_codex_provider" --include="*.py" kagweb/ | wc -l   # 0
# 残留的 catalog profile 仍安全：owner-bound = 永不可授予
grep -n "OWNER_BOUND_BINDINGS" -A 3 kagweb/multi_user/model_access.py

# KAG 管理面（新增于 v0.2.2）
ls kagweb/services/kag/ kagweb/api/routers/kag.py
grep -n "include_router(kag" kagweb/api/main.py   # /api/kag 与 /api/kag/bridge
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

> **⚠ 本节是 `ef8bbf5` 时期的复核记录，其「仍然成立」表此后已再次漂移**（表中「Skills 资产、人格预设、合伙人代码均未变」等项现均已移除；「9 个 LLM 调用点」现为 8 个）。**当前状态的权威复核见 §八**，本节仅作历史留档。

提交时远端（`gitee.com/wustbd/kagweb`）已领先 **20+ 个提交**，其中包含大量 `services/agent_loop/` 改动（新增 `acp_backend.py` 692 行、`http_backend.py` +295、`protocol.py` +45；`capabilities/chat/capability.py` ±217；`web/locales/en/app.json` −4,668 行）。本文基准 `c3ffe57` 因此**部分过时**，逐条复核结果如下。

**经复核仍然成立的断言**（本轮于新 HEAD 重新实测）：

| 断言 | 复核方式 | 结果 |
|---|---|---|
| `AgentLoopRequest` 无 `model` / `tools` 字段 | 字段全集 | 仍为 `prompt/history/session_id/language/workdir` ✅ |
| `ChatCapability` 对 LLM 层零依赖 | `grep -c get_llm_config\|llm_selection` | **0** ✅ |
| `get_tool_schemas()` 零调用者（P0-2 决定性证据） | 全仓库 grep | 仅定义处 ✅ |
| 轮次门禁仍硬编码 `has_capability_access("llm")`（P0-1） | `request_preparer.py` | ❌ **已不成立**：2026-09-10 起按 `required_service` 分派（§1.2） |
| 9 个 LLM 调用点 | 逐文件 grep | ✅ 旁路调用点当时未变、主路径仍零依赖（**注**：`aad0eb1` 已降为 8 个，见 §八） |
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
> **✅ 已处理（2026-09-11）**：能力目录收缩落地——`CHAT_CAPABILITIES` 砍到只剩 `chat`（含描述诚实化），`ALL_TOOLS`/`allowedTools`/`defaultTools` 与 `state.enabledTools` 写不读状态链整删，`readChatLaunchIntent` 的 `tool` 参数随之移除；`mergeCapabilityPresentations`/`visibleCapabilityPresentations` 保留（插件能力的展示通道，遗留 id 以 `HIDDEN_CAPABILITY_IDS` 硬排除）。

**复核实为准确、无需修正的项**（**成文时口径**，多项此后已变，勿直接引用）：`request_preparer.py:105-134` 门禁代码；`capabilities/chat/capability.py:112-122` 无 LLM 依赖；`get_tool_schemas()` 零调用者（`orchestrator.py:179`）；`codex_auth` 与 `agent_loop` 无关联；9 个 LLM 调用点行号；`ChatRequestConfig(EmptyConfig)` 零字段；`AGENT_LOOP_INTELLECT_PRESETS` 值；`question_bank` 读路径孤立 / 写路径活跃。

> **⚠ `aad0eb1` 复核**：其中 `get_tool_schemas()` 零调用者（方法已删）、`AGENT_LOOP_INTELLECT_PRESETS` 值（枚举已删）、9 个调用点（已降为 8）三条**已不适用**；`request_preparer` 门禁代码已重构。权威状态见 §八。

**本文件所有精度声明**：行号与计数均为 2026-09-10 于 `c3ffe57` 实测；会随代码漂移的计数（locale 键数、代码行数）已在原处标注实测口径。

---

## 七、多用户身份：KAGWeb 账号如何映射到 Intellect 账号

**背景**：KAGWeb 与 Intellect 是两个各自持有账号体系的系统。此前的对接里，每一轮对话都以同一个部署级密钥（`API_SERVER_KEY`）发出，Intellect 无法区分是哪个 KAGWeb 用户发起的——所有会话与 run 都归属同一主体。

**关键事实（已核实）**：Intellect Gateway 的 `sessions.member_id` **没有外键约束**（`intellect-storage/migrations/20260615000001_initial_schema.sql:39`），且 Profile 模式（部署密钥）会把 `X-Intellect-User` 头直接写入 `ctx.member_id`（`auth.rs:386-403`，且该判定**早于** members 开关）。所以"把 KAGWeb 用户映射成 Intellect 侧主体"**不需要预先在 Intellect 建账号**。

### 两种桥接，性质不同

profile 的 `identity_mode` 字段决定用哪种。默认 `off`，即与升级前完全一致。

| 模式 | 发送内容 | 效果 |
|---|---|---|
| `off` | 仅 profile 的 `api_key` | 调用方信息完全不出现在服务端 |
| `header` | 部署密钥 + `X-Intellect-User: mem_<账号>` | **归属**：服务端记录每轮 run/session 的 owner |
| `token` | 该用户自己的成员令牌 | **委托**：服务端按角色与 owner 做隔离；未关联者回落 `header` |
| `token_required` | 同 `token`，但强制 | 未关联用户无法发起对话 |

**`header` 只是归属，不是隔离。** 部署密钥携带的是不受限主体（`bypass_member_filter=true`），所有归属校验都会短路：`RunEntry::allows()` 无条件返回 true（`run_state.rs:301-304`），`get_session_for_actor_ext` 也提前返回（`session_store.rs:645-646`）。也就是说，**持有该密钥者可按 id 读取任意会话与 run**。真正由服务端强制隔离的只有 `token` / `token_required`。

由此，`session_prefix`（`<owner_id>:` 前缀）是**安全控制**而非整洁性：它是防止两个账号在远端撞同一个 session 的唯一屏障。同理，Profile 模式会设置 `cfg.user_id = auth.member_id`（`api_server.rs:601`），从而触发 `loop.rs:3251` 的访问校验——不加前缀反而可能出现误判拒绝。

### 关联失效绝不降级

这是本设计的硬规则：已关联用户的凭据若过期/被吊销/被拒绝，**该轮直接失败并给出提示，绝不回落部署密钥**。因为回落目标（部署密钥）比刚刚失效的令牌**权限更大**——"好心"降级等于在限制刚开始生效时给用户提权。只有"从未关联"才可回落（且由部署选择 `token` 的宽松回落或 `token_required` 的 fail-closed）。

### 凭据存放与生命周期

- 路径：`data/system/user-secrets/<owner>/private/intellect-agent/`，目录 0700、文件 0600，与 Codex OAuth 凭据同级同构。
- **不放工作区**：沙箱的 `exec` 能读工作区，而 `data/system` 是唯一不挂载的树（`multi_user/paths.py:219-232` 的既有理由）。
- 记录内含 `kagweb_user_id`，读取时与当前账号比对——备份恢复或复制到他人目录的文件会被忽略，而不是被借用。
- **删除用户会清理该目录**：`delete_user` 原本只移除 `users.json` 记录，凭据会一直留到自然过期，并被下一个复用该 id 的账号读到；本次补上了清理。
- 登出/解绑时 best-effort 调 `POST /api/members/logout` 吊销；服务端不可达时仍必须能解绑（否则离线用户被锁在链接里）。无 refresh 端点，续期即重新关联。

### 界面与 API

- 卡片位于 **Settings → 模型**（与 Codex OAuth 卡片并列），**不是** Agent Backend 分区——后者是 `adminOnly`，而这是个人凭据。无配置服务时卡片自行隐藏。
- 接口：`GET/POST/DELETE /api/settings/agent-loop/identity`，**刻意不做管理员门禁**（沿用 Codex OAuth 生命周期的先例）。响应**永不回显令牌**。
- 目标服务地址取自部署的 primary profile，**不接受用户指定**——否则这个表单就成了任意主机的外发请求入口。
- 两种关联方式：Intellect 登录名+密码（换取令牌后立即丢弃明文，不落盘不记日志）或直接粘贴令牌；两者都会先向服务端校验再保存，使错误的凭据在此刻就报错，而不是在很久以后表现为一轮失败的对话。

### 尚未覆盖

- 不把 KAGWeb 登录改为 Intellect 登录：KAGWeb 仍是唯一 IdP，`imt_*` 只用于**出站**表达身份，绝不用于认证任何 KAGWeb 请求。合并登录只发生在用户主动关联的那一次。
- 不自动预置 Intellect 用户；Tier 2 需 Intellect 侧存在成员（或用户自助注册，受 `members.enabled` 约束）。
- `run.completed.usage` 只有 token 计数、无 `cost_usd`，故结果页仍不显示成本卡片。
- 预算耗尽被 Gateway 归约为通用 `run.failed`（`api_server.rs:5397-5409` 有意不泄漏内部错误），无法映射为 KAGWeb 的 `incomplete_reason` 截断标记。

### 令牌与服务的绑定（安全加固，评审后补）

**问题**：profile 的 `url` 是管理员可改的部署配置。若用户令牌只按"当前 profile 的 URL"外发，则**改一次 profile URL（或改选另一个 primary）就会把所有已关联用户的令牌送到新主机**——这是一条真实的凭据外泄路径，评审中已用探针复现（`https://attacker.example` 会收到 `Bearer imt_SECRET`）。

**修法**：令牌绑定签发它的服务 origin（scheme + host + port）：

- 关联时记录 `service_origin`（规范化：忽略路径与默认端口，`https://a/x` 与 `https://a:443` 同源；scheme 降级与端口变化视为不同源）。
- 解析时只回发给该 origin；不匹配则**报错要求重新关联**，既不发送也不回落部署密钥（与"失效不降级"同一条规则）。
- 旧版本写入的、未记录 origin 的链接**一律不使用**（无法判断它属于哪个服务），用户重新关联一次即可。
- 界面据此把 `stale_reason` 区分 `expired` / `service_changed`，两种都提示"重新连接"。

**另外两处**：

1. **密码路径禁止明文外发**：密码是用户**可复用**的凭据（不同于管理员可轮换的服务密钥），因此当服务 URL 是非 loopback 的 `http://` 时拒绝密码登录（提示改用 HTTPS 或令牌）；loopback 不受限。令牌路径不限制——令牌本身就是为轮换而生的。
2. **异常响应不再 500**：URL 误指向登录门户/Ingress 页时服务端会以 200 返回 HTML，此前会抛 `JSONDecodeError`（表现为无信息的 500）。现在统一解析为"该地址不是智能体服务"，指向真正的配置错误。

### 跨站请求防护（评审后补）

**问题**：这些端点是 cookie 鉴权的、且**不要求管理员**（个人凭据，沿用 Codex OAuth 先例）。用探针核实：跨站 JSON POST **会在服务端执行并返回 200**——浏览器是否允许读取响应并不影响副作用。而 KAGWeb 的 CORS 在 auth 关闭（默认）时是宽松正则（`https?://.*`），预检直接放行。因此"关联"端点可被跨站触发，危害是**把受害者绑定到攻击者的 Intellect 身份**，此后受害者的对话都在攻击者名下运行。

**澄清**：经典 HTML 表单 CSRF 不成立——FastAPI 对非 JSON content-type 返回 422，表单只能发 urlencoded/multipart/plain，够不到 `BaseModel` 体。真正的通路是跨站 `fetch` + JSON。

**修法**：不依赖 CORS，改为校验请求自身的 `Origin`（`origin_is_trusted`）：

| 情形 | 处置 |
|---|---|
| 无 `Origin`（curl / SDK / 测试） | 允许——非浏览器表单 |
| `Origin: null`（沙箱 iframe / `file://`） | **拒绝**——洗白跨站请求的惯用手法 |
| 与请求 `Host` 同源 | 允许（**忽略 scheme**：TLS 常在应用前终止，浏览器 `https://h` 与代理转发的 `Host` 可能只是 scheme 不同） |
| 在显式配置的 CORS origin 列表内 | 允许——前端另域部署的运营者不受影响 |
| 配置了 `*` | **不豁免**——`*` 即"任意站点"，正是所防的情形 |

已加在：`POST/DELETE /api/settings/agent-loop/identity` 与 Codex OAuth 的 `start`/`cancel`/`logout`（同类暴露——建立或销毁凭据；`status` 是 GET，`models/refresh` 只改模型目录，未加）。

### `member_id` 折叠碰撞（评审后修）

`member_id_for` 原先只做字符折叠，**不是单射**：`a/b` 与 `a-b` 都折成 `a-b`，两个账号会被归因到同一服务端主体。而 `user_id` 在 `multi_user/context.py` 里可回退为**用户自选的用户名**，所以这条路径可达。现改为：仅当折叠**确实改变了** id 时，追加原值的 8 位摘要（并把可读部分截断以适配 64 字符上限）。普通 `u_<hex>` 与 `local-admin`/`env-admin` 输出**逐字节不变**，判定按 id 逐条进行，因此异常账号也不会与正常账号相撞。

> 影响面说明：仅影响**归属标记**，不影响隔离——会话命名空间用的是原始 owner id，不是 member id。

---

## 八、基准漂移总复核（2026-09-20，tag `v0.2.2` / 实测于 `aad0eb1`）

**本节目的**：本文正文成文于 `c3ffe57`（2026-09-10），此后仓库推进了 **150 个提交**（至 tag `v0.2.2`）。为避免「按正文操作却对不上代码」，这里把**所有已失效的结论一次性列清**，并给出当前状态的权威断言。**本节优先级高于正文任何与之冲突的表述。**

### 8.1 批次执行结果（§六优先级表的最终态）

> **本节成文并实测于 `aad0eb1`，即 tag `v0.2.2` 的代码**（2026-09-20）。相对前一版基准（`3bf3464`）的两处变化：KAGWeb 新增 KAG 管理面（`services/kag/` + `/api/kag`）；`services/codex_auth/` 与 `openai_codex` provider **退役**（提交 `ff761c7`）。后者又关闭了本文的一项建议（§四 #10 的凭据打通）。

| 批次 | 内容 | 结果 |
|---|---|---|
| 批次零 | Intellect 对接修复（7 项） | ✅ 全部落地 |
| 批次一 | 死字段/死 UI 清理、设置导航重构 | ✅ 全部落地 |
| 批次二 | 门禁解耦 + `agent_loop` / `agent_loop_cli` 授权维度 | ✅ 全部落地 |
| 批次三 | profile `model` / `context_window` | ✅ 全部落地 |
| 批次四 | 合伙人移除、人格移除、`.md` 规则删除 | ✅ 全部落地 |
| 批次五 | 学习者/监护人移除、工具层移除、MCP 撤退 | ✅ 全部落地 |
| 批次六 | Intellect 多用户身份桥接（§七） | ✅ 已实现（`agent_loop/identity.py`，4 种 `identity_mode`） |
| 批次七 | codex 凭据链路退役（P1-4/P3 结案） | ✅ 已落地（`ff761c7`；含凭据残留核查） |

**八条产品决策的落地状态**：决策 1（KAGWeb 作门面）✅ 定位已写入 `AGENTS.md`/`ARCHITECTURE.md`；决策 2（Agent Backend 顶级项）✅；决策 3（Skills/Tools 移除）✅ **含资产**；决策 4（学习表述清理）✅；决策 5（LLM 设置门控）✅ 判据改为「自托管 HTTP 服务」；决策 6（人格移出）✅；决策 7（合伙人移除）✅；决策 8（Intellect 校正）✅ Rust-only。

### 8.2 已失效的正文结论（**勿再引用**）

| 正文位置 | 旧结论 | 现状 |
|---|---|---|
| §结论摘要 / §1.1 | 「schema 里连 `model` 字段都没有」 | ❌ `AgentLoopRequest` 有 `model`（`protocol.py:94`） |
| §1.1 / §三 P0-2 | 「`AgentLoopRequest` 没有 `model`，也没有 `tools`」 | ❌ 前半句已失效；后半句成立但**理由变了**——不是「待打通」，而是工具层已被删除 |
| §1.2 | 门禁硬编码 `has_capability_access("llm")`，可被固定 `llm_selection` 绕过 | ❌ 已解耦（`request_preparer.py:42`、`:186-196`），见 §1.2 的「历史」注 |
| §三 P0-2 | 「工具层不可达，但 UI 上可开关」 | ❌ 工具层与开关**都已不存在** |
| §三 P1-2 | 「`ChatCapability` 完全不读 `llm_selection`（grep 无命中）」 | ❌ `capability.py:741-750` 现会解析它并以 `{model}` / 请求体下发 |
| §三 P2-3 | 错误文案指向 Catalog | ❌ 文案已改写（`llm/config.py:200-207`） |
| §五 决策 5 | `AGENT_LOOP_INTELLECT_PRESETS` 集合不完整（缺 `intellect-runs`） | ❌ 该枚举已删除（`runtime_settings.py:103` 注释说明它是「第二真源且已漂移」）；改为 `is_intellect_preset()` 前缀匹配 + `llm_settings_apply()` 按 transport |
| §五 决策 5 | `intellect-runs` 是独立预设 | ❌ 已并入 `intellect` 作为 `transport="http"` |
| §五 决策 6 | `skill-slug.ts` 等前端文件随决策 6 删除 | ✅ 已执行（该文件与 `PersonasSection.tsx` 均不存在） |
| §五 3a | 5 个 `SKILL.md` 与 3 个 `PERSONA.md` 共 8 个 `.md`；删规则前须先删资产 | ✅ 已执行：`git ls-files 'kagweb/**/*.md'` → **0 个**；`pyproject.toml` 唯一 `.md` 规则为 `kagweb_cli = ["**/*.md"]`（保留，指向 `kagweb_cli/README.md`） |
| §五 决策 7 | 合伙人约 42,000 行，需先迁移 `safe_filename` | ✅ 已执行且零残留；`safe_filename` 的迁移前置已完成（模块级导入不再指向已删目录） |
| §七 | `identity_mode` 的 4 种模式 | ✅ 仍准确（`identity.py:52` `IDENTITY_MODES = ("off", "header", "token", "token_required")`） |
| §二 | 9 个 LLM 调用点 | ⚠️ 现为 **8 个**（工具层 2 处删除、`doctor.py` 1 处补入），见 §二 |
| §1.5 / P1-4 / §四 #10 | 「凭据双份互不相通」，建议打通或维持 | ❌ **以退役结案**（2026-09-19）：`services/codex_auth/` 与 `openai_codex` provider 已删除，无 token 可打通 |
| §五 决策 4 等 | 提到 codex OAuth 卡片、`codex_auth` 的消费方 | ❌ 该卡片与账户链路已移除（38 对 `codex.oauth.*` 词条一并清理） |
| §六 优先级表 | 「合伙人/人格未做」「工具层定位待定」 | ✅ 均已完成（见 8.1 批次四/五） |
| §六 P3 检测增强 | 「CLI 探测刻意不做 `--version`」 | ⚠️ ACP 族已加 `probe()` 握手探测；CLI/HTTP 族仍不做 |

### 8.3 复测确认仍成立的结论

以下在 tag `v0.2.2` 的代码（实测于 `aad0eb1`）逐条复测通过，可放心引用：

- **主对话路径对 LLM 层零依赖**（`capability.py` 中 `get_llm_config` 零命中）。
- **`AgentLoopRequest` 仍无 `tools` 字段**——且这是**终态**：KAGWeb 不再持有工具实现。
- **`services/agent_loop/` 零 import `codex_auth`**——现在是**平凡成立**（该模块已被删除），但结论（CLI 族自带登录态、与 KAGWeb 凭据无关）不变。
- **子进程环境白名单**（`cli_backend.py:113` `_build_child_env`）与「CLI 族自带登录态」的假设（放行 `HOME`/`APPDATA`/`USERPROFILE`）——ACP 族复用同一函数。
- **`services/parsing/`、`services/voice/`、`services/imagegen/`、`services/agent_loop/`、`multi_user/` 无 LLM 调用**。
- **`NoModelConfiguredError` / `LLMConfigError` 的分层语义**（`llm/exceptions.py:32-38`）：前者被 executor 捕获、后者不被捕获。
- **`SERVICE_NAMES` 含 `embedding`/`videogen`、两者无解析函数**。
- **§七 的全部安全设计**（归属不隔离、失效不降级、令牌绑定服务 origin、`Origin` 校验、`member_id` 折叠摘要）——本轮复核代码均在位。

### 8.4 规模变化（供交叉参考）

| 项 | `c3ffe57` | `ef8bbf5` | tag `v0.2.2` |
|---|---:|---:|---:|
| 后端 Python 文件 | 412 | 340 | **323** |
| 后端 Python 行数 | 98,169 | 72,217 | **69,860** |
| HTTP 端点 | 214 | 126 | **121** |
| WS 端点 | 3 | 1 | **1** |
| 测试文件 | 221 | 194 | **185** |
| `services/agent_loop/` 行数 | 1,765 | 3,219 | **6,048** |

> 详细口径与复现命令见 [`backend-architecture.md`](./backend-architecture.md) 的「规模实测」与「验证」两节。注意 **`services/agent_loop/` 是唯一逆势增长的模块**（ACP 族、身份桥接、opencode 续接），这与决策 1「KAGWeb 作 agent backend 门面」的方向一致：**裁剪的是 KAGWeb 自己实现 agent 的部分，增厚的是对接 agent 的部分。**
>
> HTTP 端点在 tag `v0.2.2` 回升到 121（较 `ef8bbf5` 的 126 只差 5）：新增的 KAG 管理面（`/api/kag` 14 个端点）抵消了大部分此前的移除量。
