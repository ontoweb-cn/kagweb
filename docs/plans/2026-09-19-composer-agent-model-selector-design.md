# Composer 顶部 Agent Loop 模型显示与切换 — 可行性分析与设计方案

日期:2026-09-19
状态:已批准并实施(2026-09-19;评审修订见 §5a,评审中发现并修复 auth.py `model_selector_enabled` 恒 False 的潜伏 bug)

## 0. 需求

在 chat composer 区显示当前 agent loop 所用的模型,并允许用户按回合切换。要求分析后端可行性
并给出设计:首批为 intellect-agent、Claude Code、Codex(§3.1–3.4),评审中追加
Hermes(§3.6);opencode/custom-cli 顺带覆盖(§3.5)。

## 1. 现状盘点(代码已到什么程度)

按回合选型的**管线已经端到端存在**,缺的是"选项从哪来"这一段:

| 环节 | 位置 | 状态 |
| --- | --- | --- |
| Composer 选择器 UI | `web/components/chat/home/ModelSelector.tsx`,由 `ChatComposer.tsx:814` 渲染 | ✅ 完整(含"系统默认"行、悬停展开、错误态) |
| 显示开关 | `/api/auth/status` → `model_selector_enabled` ← `profile_per_turn_model(primary)`(`kagweb/api/routers/auth.py:443`) | ✅ |
| 选项来源 | `/api/settings/llm-options` = KAGWeb **对话 LLM 目录**(provider profiles + API keys) | ⚠️ 词汇错配的根源(见 §2) |
| 回合携带 | `llm_selection`(profile_id+model_id)→ `TurnRequest` → request_preparer 授权校验 → executor 放进 `UnifiedContext.metadata` | ✅ |
| 名字解析 | `resolve_agent_model_for_selection`(`model_selection/llm.py:182`)在目录中查出具体模型名 → `AgentLoopRequest.model` | ✅ |
| CLI one-shot 应用 | `_render_extra_args` 把 `{model}` 替换进 profile args(`cli_backend.py:574`);优先级 回合选择 > profile.model > 后端默认 | ✅(预设 base_args 不含 `--model`,依赖操作员写 `--model={model}`) |
| ACP 应用 | `_apply_turn_model` → `setSessionConfigOption(config_id="model")`(`acp_backend.py:865`),fail-soft | ✅ 已实现,**但 transport 仍标 `per_turn_model=False` → 选择器被隐藏** |
| HTTP runs 应用 | body `model` 键(`http_backend.py:341,670`) | ✅;上游两侧已消费(#126 已修复,Python 侧带 `invalid_model` 校验) |

设置页侧:profile 已有单值 `model` 字段和 `context_window`;
`effective_primary.per_turn_model` / `llm_settings_enabled` 已在 `GET /api/settings/agent-loop` 暴露。

本机实测现状:primary = Intellect ACP(profile.model=deepseek-flash)→ `per_turn_model=False` →
composer **不显示**选择器;LLM 目录为空(即使显示也没有选项)。

## 2. 核心矛盾

选择器的**选项**来自对话 LLM 目录,而目录对多数后端是错误词汇表:

- **CLI 族(claude-code/codex)**:子进程自带登录与自身配置,目录里的 API key 毫无意义;
  `llm_settings_apply` 对 CLI 族刻意返回 False(设置页隐藏 LLM 叶子,理由见 `builtin.py:381` docstring),
  但 composer 仍会对同一后端显示目录派生的选项 —— 设置页与 composer 自相矛盾(即 backlog 里的
  "Models 门控矛盾"残留)。更糟:非管理员回合会被 request_preparer 自动钉上第一个已授权目录模型
  (`request_preparer.py:229`),CLI 会拿到一个它根本不认识的 `--model=…`。
- **词汇取决于子进程自身配置**:本机 `~/.claude/settings.json` 的 model 是 `deepseek-flash`,
  `~/.codex/config.toml` 的 base_url 指向 GLM 网关 —— 任何按厂商硬编码的模型清单
  (Anthropic sonnet/opus、OpenAI gpt-5-codex)在这种部署里都是错的。
- **Intellect ACP**:能力两侧其实都已就绪(Intellect v0.7.0 经 `configOptions` 广播 Model 选择器,
  KAGWeb 已实现 setSessionConfigOption),却因标志位 False 而整个隐藏。
- **Intellect HTTP**:`llm_settings_apply=True`,目录语义在这一族才是对的(凭据随对话设置下发);
  但凭据打通是 P3 挂起项,目录目前为空。

结论:问题不是"能不能传模型"(管道全通),而是**选项来源必须按后端分族**。

## 3. 各后端可行性结论

### 3.1 Intellect ACP(本地 `intellect acp`)— 可行,且是收益最大的一族

证据链(intellect-agent v0.7.0,KAGWeb SDK 0.12.1):

- 服务端 `acp_adapter/server.py`:`_build_model_config_options` 在 newSession/loadSession 响应里
  广播 `SessionConfigOptionSelect(id="model", category 含 model 语义)`,含
  `current_value` + `options[]`(value 为 `provider:model` 编码,列表来自
  `curated_models_for_provider(当前 provider)` + 当前模型)。
- 服务端 `set_config_option("model")` → `_apply_model_switch`:支持 `provider:model` 与裸模型名
  (`parse_model_input`),切换即时重建 agent 并随会话持久化。
- SDK 侧:`schema.py` 有 `SessionConfigOptionSelect`/`configOptions`;KAGWeb `_apply_turn_model`
  已经在每回合调用 `set_config_option`。
- KAGWeb 侧还有现成的 **probe 机制**(`acp_backend.probe()`,一次性握手子进程)可复用来读
  config options,不必每次打开选择器都拉起会话。

工作量:transport 标志翻 True + 握手时读取/缓存 config options + 值直传。**真实清单、真实当前值**。

### 3.2 Intellect HTTP(intellect `http` transport / intellect-team)— 可行,管道已通

- `POST /v1/runs` body `model`:Python 适配器按回合覆盖并校验(未知名报 `invalid_model`,
  见 `adapter.py:3880` 一带);Rust api_server 的 `req.model` 在请求结构中流转并参与幂等指纹。
  KAGWeb backlog 里的 #126 已被上游修复,ARCHITECTURE.md 的矩阵描述与现状一致。
- 选项来源:上游没有可查询的模型目录端点(`/v1/models` 是 OpenAI 兼容 shim,只报 intellect-agent)。
  现实来源 = profile `model` + 新增的 profile 级 `models` 清单(§4);名称必须与该部署
  config.yaml 的模型名一致,否则回合 400。
- 这一族保留对话目录作为合法选项源(`llm_settings_apply=True`),待凭据打通(P3)后自然合流。

### 3.3 Claude Code(one-shot CLI)— 可行(机制在),清单只能配置

- `claude -p --model <alias|full>` 确认支持(本机 CLI 帮助可见)。
- 无模型列表命令;词汇取决于子进程自身配置(本机被路由到自定义 provider)。
- 应用条件:profile args 必须含 `{model}` 占位符(如 `--model={model}`),预设默认不含 →
  需要设置页校验/提示,否则选择器形同虚设。

### 3.4 Codex(one-shot CLI)— 同 3.3

- `codex exec -m <MODEL>` 确认支持;无列表命令;本机 base_url 指向 GLM 网关,词汇随子进程配置。

### 3.5 顺带:opencode / custom-cli

opencode 预设同样标 `per_turn_model=True` 且有 `opencode models` 列表命令,本期不做自动探测,
与其他 one-shot CLI 一致走配置清单;custom-cli 不变。理由见 §6 的探测判据。

### 3.6 Hermes(HTTP turn 协议)— KAGWeb 侧机制就绪;服务侧消费不可验证,以配置清单 opt-in

- **身份**:`hermes-agent` 是与 intellect-agent 同源的独立 agent 代码库(两仓共享大量祖先文件,
  intellect 的改进计划以它为参照源,见 intellect-agent 仓 `docs/plans/2026-08-31-*hermes*` 两文)。
  本机没有 hermes-agent 检出——KAGWeb 只有预设与 wire 契约,没有服务实现可供核对。
- **KAGWeb 侧:机制已就绪,零改动。** 预设 = HTTP family / turn 协议(默认
  `POST {url}/agent/turn`,中性 `{kind:…}` 帧);`http_backend.run()` 在 body 里带 `model`——
  回合覆盖 > profile `model`,未设时整个键省略(既有部署的请求体字节不变);
  `test_backends.py` 已覆盖 model 字段对 hermes 后端的穿透(`build_agent_loop_backend` 用例)。
- **服务侧:是否消费 `model` 键不可验证。** 预设按"unknown services claim nothing"标
  `per_turn_model=False`,且无 probe_url(设置页检测也够不到它);ARCHITECTURE.md 能力矩阵
  记 ⬜("honored where the service reads it")。
- **词汇来源**:深潜文档显示 Hermes 自有 provider/model 配置体系(含 `moa/<preset>` 虚拟
  provider),部署者知道模型名;但没有可查询的模型目录端点。profile `models` 配置清单是
  唯一诚实的来源——结论与 CLI 族相同,并顺带覆盖同族的 `agentscope` / `custom-http`。
- **设计增量(§4.2-8)**:HTTP turn 族的 per-turn 门改为**派生**——预设标志 OR
  profile.models 非空。理由:CLI 族的 `{model}` 替换发生在 KAGWeb 本地、可验证,预设标
  True 是可证的;HTTP turn 族的消费与否 KAGWeb 无从得知,**操作员配置清单本身就是那个
  claim**。这既保持矩阵"claim nothing"的诚实姿态,又给真消费 `model` 的 hermes 部署一条
  不改 KAGWeb 代码的点亮路径;声明错误的后果是选择器静默无效,与今天 profile.model 的
  行为一致。

## 4. 设计方案

### 4.1 原则

1. **选项来源按族分派**,选择器 UI 不变(ModelSelector 复用):
   - `acp`(intellect 本地)→ 握手广播的 config options(真实清单+当前值);
   - `http` runs(intellect 两族)→ profile `models` 清单(新增)+ 现有对话目录(该族语义成立);
   - `cli`(one-shot)→ profile `models` 清单(新增)+ profile `model` 单值;
   - `http` turn(hermes / agentscope / custom-http)→ 同 CLI 的配置清单,且清单本身是
     per-turn 门的 opt-in(§3.6);
   - 清单为空时只显示"后端默认"一行,不再拿对话目录滥竽充数。
2. **回合契约最小扩展**:`llm_selection` 增加后端原生形态
   `{"backend_model": "<value>"}`(与 profile_id/model_id 二选一)。
   `resolve_agent_model_for_selection` 对 backend_model 做白名单校验(必须在该后端当前可选项内),
   然后直传 `AgentLoopRequest.model`;ACP 传广播的 value 原文(`provider:model`),
   CLI/HTTP 传模型名字符串。
3. **门控收敛**(了结"Models 门控矛盾"残留):
   - `model_selector_enabled` 语义不变(后端能不能吃回合级模型),但 ACP transport 翻 True
     (能力已验证),并在 `builtin.py` docstring 修正"ACP has no per-turn model field"的过时表述
     → 改为"per-turn model applies via setSessionConfigOption"。
   - request_preparer 的非管理员**自动钉模型**只在 `llm_settings_apply` 为真的族执行;
     CLI/ACP 族跳过(否则会给子进程塞它不认识的 `--model`)。
4. **回退即默认**:任何一步失败(选项拿不到、值被后端拒绝)都落在"后端默认",
   与现有 fail-soft 行为一致;回合不因选型失败而失败。

### 4.2 后端改动(M1)

1. `builtin.py`:`intellect` ACP transport `per_turn_model=True`;docstring 更新。
2. settings schema(`runtime_settings.py` 归一化 + `AgentLoopProfileUpdate`):
   profile 新增 `models: [{id, name}]`(或纯字符串列表,归一化为结构化),上限如 32;
   `GET` 原样返回、`PUT` 校验去重。
3. ACP 选项发现:`acp_backend` 握手(`_spawn_session`)后从 newSession/loadSession 响应读
   `config_options`,取 `id=="model"`(或 `category=="model"`)的 select,缓存
   `{options, current_value}` 到 handle;probe 路径同样返回(带 TTL 的缓存,避免每次开选择器拉子进程)。
4. 新端点 `GET /api/settings/agent-loop/models`(登录即可,非管理员按现有 llm 授权过滤目录部分):
   返回 `{source, per_turn_model, options: [{id, name, description?, is_current}], default_label}`。
5. 选型解析:`resolve_agent_model_for_selection` 支持 `backend_model`;校验来源:
   ACP → 当前缓存的 options;CLI/HTTP → profile.models ∪ {profile.model};
   不在清单内 → 返回 ""(落默认),记 debug 日志。
6. request_preparer:自动钉模型加 `llm_settings_apply` 门(§4.1-3)。
7. 设置页校验:CLI/HTTP profile 若 `per_turn_model` 且 args 无 `{model}`(CLI)或 models 为空
   → agent-loop 设置卡显示提醒 badge(不阻断)。
8. HTTP turn 族 per-turn 门派生规则(§3.6):`per_turn_model = 预设标志 OR
   profile.models 非空`;`profile_per_turn_model`、`/api/auth/status`、设置页 payload
   同步走该派生;`test_backends.py` 的期望表补断言(含"清单为空仍 False"反例)。

### 4.3 前端改动(M2)

1. composer:`model_selector_enabled` 时改为调新端点(useLLMOptions 旁新增
   `useAgentLoopModels`,共享缓存/刷新模式);`source=="catalog"`(intellect HTTP)才回落
   `/api/settings/llm-options`。
2. ModelSelector:`allowSystemDefault` 打开("后端默认"行,文案 per 后端:如 "Intellect 默认" /
   "Claude Code 配置默认");选项行复用现有布局(ProviderIcon 可省,description 进 title)。
3. 当前值展示:ACP 有 `is_current` → 触发行显示当前模型名;无当前值时显示"后端默认"。
4. 会话内记忆:沿用 `llmSelection` 持久化通道,存 `backend_model` 形态;换后端/换会话时清空
   (选项属后端,不可跨 profile 复用 —— 与现有 `asLLMSelection` 恢复逻辑互补)。

### 4.4 文档与测试(M3)

- ARCHITECTURE.md 矩阵:ACP 行 ✅(机制 setSessionConfigOption),CLI 行补
  "选项来自 profile.models",HTTP turn 行补"清单 opt-in 规则";§Per-turn model selection
  补选项来源分族表;turn 契约的示例 body 补 `model` 键(矩阵已提,示例遗漏)。
- 单测:解析器(backend_model 白名单)、归一化(models 字段)、端点(各 source)、
  request_preparer 自动钉门控;ACP config options 解析(用固定握手响应 fixture)。
- 组件测试:composer 门控与"后端默认"行(dist/node-tests 已有 chat 组件测试基建)。
- 流程红线:Python 改动先 `ruff format`;前端改动经 launcher 重建 `.next-kagweb`;
  CI 只认 github 远程。

## 5. 分阶段任务清单(待批准)

| 阶段 | 内容 | 交付判据 |
| --- | --- | --- |
| M1 后端 | §4.2 全部 8 项 | 新端点对三种 source 各返回正确载荷;pytest 全绿 |
| M2 前端 | §4.3 全部 4 项 | 本机实例:Intellect ACP 显示真实模型清单并可切换;CLI 后端显示配置清单;"后端默认"可选回 |
| M3 文档/收尾 | §4.4 | CI 绿(双远程);ARCHITECTURE.md 与实现对齐 |

## 5a. 实施前评审修订(2026-09-19,复读代码后定稿)

1. **wire 形态偏差**:`backend_model` 不作为 `llm_selection` 的联合形态,而是
   `TurnRequest` 的**兄弟字段** `backend_model: str | None`。原因:`TurnRequest` 为
   `extra="forbid"`、`LLMSelection` 是严格 dataclass(`profile_id`+`model_id` 必填),
   联合形态要改 `LLMSelection` 不变式,波及 `apply_llm_selection_to_catalog` 等全部调用点;
   兄弟字段零侵入。回退形态:消息快照仍走 `llmSelection` 键,存
   `{"backend_model": "…"}` 联合形态,前端 `asLLMSelection` 解析两种。
2. **端点定稿**:`GET /api/settings/agent-loop/models` 返回
   `{per_turn_model, source, backend_label, options}`;`source ∈ acp | profile | catalog | none`,
   **不含** `default_label`("后端默认"文案由前端 i18n 渲染)。`source=="catalog"`
   (intellect HTTP 且对话目录非空)时 options 为空,前端回落现有
   `/api/settings/llm-options`(授权过滤、activeDefault 行为全部保持);目录为空则落
   `profile`(profile.models ∪ {profile.model})。ACP 列举失败(握手不可用)同样落 `profile`。
3. **ACP 当前值语义**:`options[].is_current` 按**请求会话**解析——带 `?session_id=` 且该
   会话有活 handle 时用其握手/`set_config_option` 响应里的 `current_value`;否则经
   probe(一次性子进程)取 agent 默认,结果按 config_key 做 60s TTL 缓存,约束子进程
   拉起频率。probe 复用现有 `_PROBE_TIMEOUT_SECONDS` 预算。
4. **白名单落点**:实现为 `AgentLoopBackend.filter_turn_model(value) -> str`,由 chat
   capability 在构建 `AgentLoopRequest` 时调用(`_build_request` 同时持有 backend 与
   profile)。CLI/HTTP 严格校验(候选 = profile.models ∪ {profile.model},不在候选 →
   "" 落默认 + debug 日志);ACP 在无缓存 options 时放行(下游
   `set_config_option` 本就 fail-soft),有缓存时按 option values 校验。
5. **派生规则落点**:HTTP turn 族的「预设标志 OR models 非空」实现在
   `agent_loop/settings.profile_per_turn_model`(不是 `builtin`,后者保持纯预设真值),
   这样 `/api/auth/status`、设置页 payload、新端点三处自动一致。
6. **会话恢复**:`asLLMSelection`(前端)与快照解析接受联合形态;
   `session.preferences?.llm_selection` 读点向后兼容(后端无写入路径,属遗留读点)。
7. **SDK 面**:app facade 的 `TurnRequest` 使用方同步获得 `backend_model`
   (to_payload 透传),不另开 SDK 参数。

## 6. 明确不做(本期):自动探测 CLI 子进程的模型词汇

判据:**有协议级契约的探测才做,没有契约的不做。** ACP 本期就做了自动探测
(握手广播的 config options,带 `current_value` 与候选清单,是协议保证的真实可用集)——
分界线在这里,不在"想不想探测"。

- **claude-code / codex:没有可枚举的清单来源。** 两个 CLI 均无"列出模型"的命令;
  剩下的途径只有读子进程私有配置文件(`~/.claude/settings.json`、`~/.codex/config.toml`):
  1. 配置里只有默认模型**一个现值**,不是可选集合——"默认"已由选择器的"后端默认"行覆盖,
     探测它不新增信息;
  2. schema 是私有实现细节,随 CLI 版本漂移,另有 per-project / env / 多 provider 段覆盖,
     可靠解析 = 替厂商维护配置解析器,且坏时静默;
  3. 探测结果填不满选择器的最小契约 {id, name, is_current}:读到的名字没有"当前可用"语义;
  4. 向 provider API 查 `/v1/models` 需要凭据,而 CLI 族的设计前提恰是 KAGWeb
     永不触碰子进程登录态——这步越过信任边界。
- **opencode:命令存在,输出不对口。** `opencode models` 列的是 models.dev 注册表全量目录
  (数百个 provider×model),不是"该部署应 offering 什么"——下拉框终需人工策展,探测结果仍要人筛;
  本机未装 opencode,输出格式无法实测,新增运行时探测路径只能 mock 验证;
  该预设走 generic translator,低频路径,单开探测+缓存代码收益/风险比不划算。
- **配置清单不是降级替代**:对 CLI 族它本来就是正确 UX。探测唯一有价值的后续用途是
  "从 `opencode models` 导入"的填充辅助——那是设置页的 UI 便利,不进运行时契约,
  后期添加无需改动任何字段或 wire 形态。
- **Intellect HTTP 的服务端模型目录查询端点**(上游没有,推动上游另立 issue)。
- **凭据打通(P3)** 维持挂起,不在本期。

## 7. 实施与评审记录(2026-09-19 定稿)

| 提交 | 内容 |
| --- | --- |
| `1470a3a` | M1+M2+M3 全量实施(§4.2 按 §5a 偏差执行);评审中发现并修复 `model_selector_enabled` 恒 False 的潜伏 bug(`RuntimeSettingsService.load()` 不存在);CI run 35411424538 全绿 |
| `efc6268` | 第一轮代码评审修复:catalog 模式默认行文案回归(保留「System default」)、补 §4.3-4 跨 source 陈旧选型清空(effect,后端 source 空清单时豁免)、`useAgentLoopModels` 去掉 keyless single-flight(会话切换污染,`withClientCache` 本身按 key 共享在途请求);CI run 35412257110 全绿 |
| `55835b8` | 第二轮代码评审修复:**P1 既有 bug** —— `regenerate_last_turn` 自 f4121ba(9/11 删除 tools 契约字段)起 payload 仍带 `"tools"`,`TurnRequest` extra=forbid 把所有重新生成请求拒绝(extra_forbidden),WS regenerate 分支只捕 RuntimeError,ValidationError 直接逃逸;同修复 regenerate 对 `{"backend_model": …}` 快照形态的恢复(否则按回合选型的回合无法重生成);CI run 35413330131 全绿 |

验收记录:pytest 1723+ 全绿(新增 ACP 广播/过滤/端点/门控/regenerate 回归约 25 用例);web node 689 + vitest 51;本机实例实测 `/api/auth/status` `model_selector_enabled=true`、models 端点返回真实 ACP 广播清单(`deepseek:deepseek-flash` current + `deepseek-v4-pro`)。

评审确认的非问题:`StartTurnCommand` 继承 `TurnRequest`,WS 契约自动放行 `backend_model`;CLI `{model}`/`{prompt}` 哨兵单遍替换,模型值不会把 prompt 泄进 argv;`invalidateClientCache` 为前缀语义;`ensure()` 失败路径不登记句柄。

## 8. 挂账任务细化(评审后实施)

评审遗留的 P3 项,细化为四个任务:

### T1 `backend_model` 长度上限(P3,卫生)

- **问题**:`TurnRequest.backend_model: str | None` 无长度上限;恶意客户端可发超大字符串,落入单个 argv 元素(`--model={model}`)或 HTTP body——无注入面、失败安全,但便宜的上限值得加。
- **改动**:`TurnRequest.backend_model` 加 `Field(default=None, max_length=256)`(pydantic 对 str 生效,WS 契约继承自动生效);超限 → 协议层校验错误,与其它字段行为一致。
- **判据**:单测——201+ 字符的 payload 被拒;契约 schema 再生后含 maxLength。

### T2 设置页校验 badge(§4.2-7 的剩余一半)

- **问题**:CLI profile 配了按回合清单但 args 缺 `{model}` 占位符时,选择器的值不会生效——目前只有编辑器描述文案提示,无显式提醒。
- **改动**:AgentLoopSettingsSection 的 profile 卡片在**保存前草稿态**校验:`preset` 为 one-shot CLI 族 && `modelsText` 非空 && `argsText` 不含 `{model}` → 显示 amber 警示(文案:「args 需引用 {model},否则按回合模型不会生效」),不阻断保存。
- **判据**:组件渲染校验(node 测试或 typecheck+人工);文案入 zh locale。

### T3 端点与 ABC 的 profile 行构造去重

- **问题**:profile 词汇 → 选项行的映射在 `GET /api/settings/agent-loop/models` 的 step-3 和 `AgentLoopBackend.list_model_options` 默认实现各写一遍(行为微差:默认实现不追加配置模型)。
- **改动**:抽取模块级 `profile_model_options(models, configured) -> list[dict]` helper(protocol.py),两处共用;行为对齐到端点现状(配置模型缺失时追加并标 current)。行为不变,纯收敛。
- **判据**:现有端点测试与 `test_factory_*` 全绿,无新行为。

### T4 probe 失败负缓存(有意不做,记录在案)

- 失败不缓存 = 瞬时故障不被钉死 60s,代价是 CLI 坏掉时每次 force 刷新重拉探测子进程(受 `MAX_ACTIVE_CHILDREN` 与 10s 超时约束)。**维持现状**,此条仅归档。
