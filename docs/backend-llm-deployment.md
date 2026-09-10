# KAGWeb 后端 LLM 部署机制分析与改进建议

> 适用版本：KAGWeb 0.2.1（`kagweb/__version__.py`）
> 核对基准：`main` 分支 `c3ffe57`（2026-09-10 实测）
> 关联文档：[`backend-architecture.md`](./backend-architecture.md)、[`../ARCHITECTURE.md`](../ARCHITECTURE.md)

> **文档结构**：§一~§四为**现状分析**（基于代码实测）；**§五为产品决策安排**（7 条方向性决策及其落地分析）；§六为优先级建议。§五的决策 6/7（人格与合伙人移除）会**反转**本文若干早期建议，反转处已在原位置标注。

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

### 1.2 轮次门禁（模型授权）

`request_preparer.py:91-134`。顺序上，门禁**先于**后端选择执行（`start_turn` 同步跑完门禁才 `create_task(_run_turn)`）。

```python
# request_preparer.py:105-134（节选）
else:
    current_user = get_current_user()
    if not current_user.is_admin:
        # Single gate, shared with the frontend lock and any HTTP surface
        if not has_capability_access("llm"):
            raise RuntimeError(
                "No LLM model is assigned to your account. Please contact an administrator."
            )
        assigned_llms = [...]
        llm_selection = {"profile_id": ..., "model_id": ...}
```

- **管理员**：完全跳过，`llm_selection` 留空 → `executor.py:333-336` 捕获 `NoModelConfiguredError` → `llm_config = None` → 轮次继续 → agent backend 正常运行。✅
- **非管理员**：`has_capability_access("llm")` 为假 → **抛错，轮次被拒**。❌

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

### P0-1 非管理员用户无法使用 agent backend（产品级阻断）

**现象**：纯 agent loop 部署下，管理员能聊天，**任何非管理员用户的每一轮都被拒绝**，错误为 `"No LLM model is assigned to your account."`

**根因**：门禁把「有可用的 LLM 授权」等同于「允许聊天」，且在 agent backend 被选择**之前**执行。chat 能力实际不需要 LLM 模型，但轮次根本走不到那里。

**证据**：`request_preparer.py:105-134`（门禁）vs `capabilities/chat/capability.py:112-122`（后端选择，无 LLM 依赖）。

**影响**：这与 `ARCHITECTURE.md` 已记录的「纯 agent-loop 部署目前只服务管理员」是同一个问题——但文档把它描述为「待产品决策的已知边界」，而实际上它是**多用户能力被完全阻断**。如果 KAGWeb 的定位是 KAG 后端的 Web 门面，这个阻断必须优先解决。

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

### P1-1 上下文窗口错配

**现象**：agent loop 部署下历史预算被钉死在最低档。

**链路**：`executor.py:337` 把 `llm_config`（此处为 `None`）传给 `ContextBuilder.build()` → `context_builder.py:159-170` `_effective_context_window()` → `resolve_effective_context_window()` → 无模型名可匹配 → 回落 `DEFAULT_CONTEXT_WINDOW_FALLBACK = 16_384`（`llm/context_window.py:7`）→ 乘 `history_budget_ratio = 0.35`。

**结果：约 5,734 token 的历史预算**，喂给一个通常有 200K~1M 窗口的 Claude Code / Codex。

`LARGE_CONTEXT_MODEL_DEFAULT = 65_536` 的「大模型多给」兜底逻辑依赖**模型名匹配**（`KNOWN_LARGE_CONTEXT_MARKERS` 含 `claude`/`gpt-5` 等），而 agent loop 部署下模型名是空字符串——**永远走最低档**。

**影响**：长会话过早触发摘要甚至截断，丢掉本该保留的上下文。

### P1-2 模型选择器指向错误对象

`llm_selection`（`core/turn_request.py:22-26`）随轮次请求下发，前端在伙伴配置等界面仍在发送。但在 agent loop 模式下：

- `ChatCapability` 完全不读它（grep 无命中）；
- 它只影响 KAGWeb 自己的 LLM 层（标题、洞察、摘要）；
- 对 agent backend **零影响**。

用户换一个模型，对话行为**毫无变化**，且没有任何提示说明这一点。

### P1-3 profile schema 没有 model 概念

`runtime_settings.py:1272-1310` 的完整字段：

```
id, name, preset, enabled, command, args, env,
url, turn_path, headers, api_key,
timeout_seconds, session_workspace, consult_enabled, workdir
```

无 `model`。CLI 后端只能靠 `args`（前端占位符正是 `--model\nbig-model\n{prompt}`，`AgentLoopSettingsSection.tsx:899`）或 `env`；**HTTP 后端连塞的地方都没有**。

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

### 阶段一：解除阻断（必须，改动小）

**1. 解耦轮次门禁与 LLM 授权**

不要让 `request_preparer` 硬编码 `has_capability_access("llm")`。让能力**声明自己需要什么资源**：

```python
# CapabilityManifest 增加
required_service: str = "llm"        # chat 在 agent-loop 模式下改为 "agent_loop"
```

`request_preparer` 据此判定：

```python
service = capability_manifest.required_service
if not has_capability_access(service):
    raise RuntimeError(f"No {service} access assigned to your account...")
```

配套：

- `empty_grant()`（`multi_user/grants.py:23-52`）增加 `"agent_loop": []` 维度；
- `model_access.py` 增加 `agent_loop` 分支（或复用 `has_capability_access` 的通用形状）；
- `ChatCapability.manifest` 在检测到 agent loop 后端时声明 `required_service="agent_loop"`。

**这是收益最高的一项**：它把「多用户 agent-loop 部署」从「不可能」变成「可用」。

**2. 诚实化工具层**

在 P0-2 解决之前，**不要**在 UI 上把工具呈现为可用。二选一：

- **短期（诚实）**：agent loop 模式下隐藏工具开关与授权项，或明确标注「当前后端不支持」。
- **长期（打通）**：见阶段三。

### 阶段二：对接 agent backend 的模型（核心诉求）

**3. profile 增加 `model` 字段**

```jsonc
{
  "id": "default",
  "preset": "claude-code",
  "command": "claude",
  "model": "claude-sonnet-5",        // 新增
  "args": ["--model", "{model}", "{prompt}"],   // CLI：占位符替换
  "context_window": 200000,          // 新增：后端真实窗口
  ...
}
```

- **CLI 族**：把 `{model}` 做占位符替换注入 argv；未配置 `model` 时保持现状（不传）。
- **HTTP 族**：请求体增加 `"model"` 字段，写进 `http_backend.py:101-106` 的契约。

**4. 打通上下文窗口**

`AgentLoopProfile` 增加 `context_window`，`ContextBuilder` 优先从 profile 取，而非从 `llm_config` 推导：

```python
def _effective_context_window(self, llm_config, agent_loop_profile=None) -> int:
    if agent_loop_profile and agent_loop_profile.get("context_window"):
        return min(agent_loop_profile["context_window"], MAX_EFFECTIVE_CONTEXT_WINDOW)
    ...  # 现有回落链
```

这直接修掉 P1-1，且让「历史预算」这个决策重新归属于**真正消费历史的那一方**。

**5. 模型选择器要么生效，要么隐藏**

- 若采纳 #3：把 `llm_selection` 映射到 profile 的 `model` 覆盖（`profile_id → agent_loop profile id`），使选择器真正生效；
- 否则：agent loop 模式下在 UI 上隐藏模型选择器，避免误导。

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

## 五、产品决策安排（7 条）

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
> | `intellect-team` | http | turn | ✅ |
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

---

## 六、优先级建议

§四的改进项与 §五的产品决策对应关系，以及合并后的执行顺序：

| 优先级 | 项 | 出处 | 理由 |
|---|---|---|---|
| **P0** | 轮次门禁解耦（`required_service`） | §四 #1 | 决定多用户部署是否可行；决策 1 的前置 |
| **P0** | 移除死字段 / 死 UI（Skills、Tools 开关） | §五 决策 3 | 消除功能性误导，改动小、风险低 |
| **P1** | Agent Backend 提为顶级设置项 | §五 决策 2 | 纯前端导航调整，无后端风险 |
| **P1** | LLM 设置按 Intellect 门控 | §五 决策 5 | 与决策 2 同一次 UI 改动完成 |
| **P1** | profile 增加 `model` + `context_window` | §四 #3/#4 | 用户核心诉求；一并修掉 P1-1 上下文错配 |
| **P1** | 人格子系统整体移出 | §五 决策 6 | 与决策 1 同源；**须早于 `.md` 打包规则移除** |
| **P1** | 合伙人与 IM 通道移除（约 42,000 行） | §五 决策 7 | 与决策 1 同源；**前置：迁移 `safe_filename`** |
| **P2** | 学习 / 研究表述清理 | §五 决策 4 | 需先就学习者/监护人功能去留做决策 |
| **P2** | 工具层与 MCP 的重新定位 | §四 #6、§五 3c | 架构方向，取决于决策 1 的落地深度 |
| **P3** | 凭据打通（codex OAuth 复用） | §四 #10 | 需架构决策（涉及把凭据交给外部进程） |

**建议的执行批次**：

1. **批次一（低风险收敛）**：决策 3 的死字段/死 UI 清理 + 决策 2/5 的设置导航重构 + 决策 4 中的纯前端部分（导航文案与图标、死词条、孤儿 prompt hints、`presentation.tsx` 的陈旧目录）。这些互不依赖，都不触碰轮次主路径。
2. **批次二（解除阻断）**：§四 #1 门禁解耦 + 配套的 `grant` 增加 `agent_loop` 维度。这是让多用户 agent-backend 部署可用的关键一步。
3. **批次三（对接能力）**：§四 #3/#4 profile 增加 `model` 与 `context_window`，打通模型配置与上下文预算。可与批次四并行。
4. **批次四（子系统移除，本体量最大）**：
   - 4a. **先迁移 `safe_filename`** 到 `kagweb/utils/`，验证 `attachment_store.py` 正常（**阻断性前置**）。
   - 4b. 决策 7 合伙人移除（约 42,000 行）。
   - 4c. 决策 6 人格移除 → 再删 `pyproject.toml` 的 `.md` 打包规则。
   - 4d. 顺带清理 `persona_context` 字段全链路、后台命令子协议、`GRANT` 的 `partners` 字段。
5. **批次五（方向性）**：决策 4 中需产品判断的部分（学习者/监护人子系统去留），以及工具层 + MCP 的重新定位。

**决策 4 的两半**：

- **可随批次一执行**：导航文案与图标（`"Learning Agent"` / `"Learning Space"` / `"Learning"` 分组 + `GraduationCap` 图标，`nav-entries.ts:6,29,39`）、死词条清理、孤儿 prompt hints、`presentation.tsx` 的能力目录。这些都无后端依赖，改动廉价且**直接改变用户观感**。
- **需先做产品决策**：默认 SOUL 模板（随决策 7 一并移除）、学习者档案 + 监护人子系统。后者是剥离后存活的**最大一块** DeepMentor（含后端、前端、测试），移除成本远高于其他项。

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
| 轮次门禁仍硬编码 `has_capability_access("llm")`（P0-1） | `request_preparer.py:121` | ✅ |
| 9 个 LLM 调用点 | 逐文件 grep | ✅ |
| `SERVICE_NAMES` 仍含 `embedding`/`videogen` 残留（P2-2） | 计数 | 仍 8 个服务 ✅ |
| Skills 资产、人格预设、合伙人代码均未变 | `ls` + 文件计数 | ✅ 全部仍在 |
| 子进程环境白名单（§1.4） | `acp_backend.py:495-497` 复用 `_build_child_env` | ✅ 新 ACP 族同样遵守 |

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
