# DeepMentor 思维链可视化能力引入 KAGWeb 方案

> 日期：2026-09-09 ｜ 状态：一轮评审已并入（见 §八）｜ 目标版本：0.3.0（未定）
> 参考：DeepMentor v1.6.5 的 `d7889cd2` / `35a834cc` / `4e1faca2` / `eab0465e`，
> 以及 `docs/cot-visualization-*.md`、`docs/chat-session-dag*.md`（DeepMentor 仓库）。
>
> **实现状态（2026-09-10 补记）**：本方案的 Phase 0–6 **均已在 main 落地**，本文自此作为
> 设计记录保留，不再是待办清单。各阶段对应提交——
> Phase 0 `cce36bf`（markdown 标签转义）/ `5474ff6`（tooltip 焦点）；
> Phase 1 `c02a0bd`（分类内核统一）/ `d0c172a`（跨语言平价锁）；
> Phase 2 `86b9d82`（turn insight 徽章）；
> Phase 3 `53e4b17`（DAG 语义缩放 + 思维导图导出）；
> Phase 4 `f45bf50`（三级披露 + observation 通道 + 学习者/专家模式）；
> Phase 5 `890feef`（耗时/Token 计数，DSL v1.1）/ `9d2c442`（时间戳兜底毫秒）；
> Phase 6 `2cdb301`（ask_user 生命周期 + CJK 强调修复）。
> 文档正文与 §八 评审记录保持原样未改。下述 Phase 顺序、改动清单与风险描述反映的是
> 当时的规划，实施中若有偏离以代码与提交为准。

## Context

KAGWeb 是从 DeepMentor 剥离学习/科研能力后保留的 Agent-WebUI 骨架，两者同源但已分叉。
DeepMentor v1.6.5 落地了一批思维链可视化改进（4 个提交 + 5 份设计文档），KAGWeb 的
trace/DAG 仍停留在更早的实现：工具行只有平铺卡片、没有回合因果结构、没有一级摘要层、
DAG 没有语义缩放与思维导图导出、没有 turn insight、ask_user 卡片只在工具返回后一次性出现。

本方案把其中**通用 agent-WebUI** 的部分引入 KAGWeb。范围 = 4 个提交 + 设计文档中标记为
通用的 CoT 项；`ask_user` 只移植**前端渲染 + 事件契约**（KAGWeb 的 chat 是外部 agent
loop，没有原生循环去流式产出工具参数）。

## 一、源端提供了什么（DeepMentor v1.6.5）

| # | 提交 | 做了什么 | 可移植性 | 耦合点 |
|---|---|---|---|---|
| 1 | `d7889cd2` | `AskUserDraftEmitter` 在模型写 ask_user 参数时发 `progress` + `metadata.trace_kind="ask_user_draft"`，卡片就地生长；落库前修 CJK 强调符号 | 前端契约 as-is；**emitter 不可移植** | 依赖原生 loop 的 tool-args 增量流 |
| 2 | `35a834cc` | 回合结束后一次廉价 judge → `turn_insight={takeaway,type}`，写入 message metadata + 实时 `trace_kind="turn_insight"` 事件 | 可移植（改提示词措辞） | 需给 store 协议加 `update_message_metadata` |
| 3 | `4e1faca2` | `turn-insight.ts`（类型→色/标签）、`dag-zoom.ts`（三档语义缩放 + 迟滞）、`thought-map-export.ts`（纯结构 SVG，构造上不含文本）、DAG 工具栏按钮、活动头徽章 | 可移植 | 依赖会话 DAG 面板（KAGWeb 有） |
| 4 | `eab0465e` | tooltip 生命周期、markdown 未知标签转义（React 19 崩溃修复）、提交被拒反馈（`command_ack accepted:false`）、**turn-insight 前端状态管道**（藏在"fix"提交里） | 可移植 as-is | 无 |

设计文档中**通用**（可引入）：分类内核统一、"DAG 与内联 trace 永不分歧"不变量、
三级渐进披露（一级摘要层）、observation 双通道、per-call 耗时/token。
（文档同列为通用的**引用悬停溯源**本方案不排期：它要先定"内联引用协议 + 悬停卡片 +
来源权限过滤"三件套，工作量和前置依赖都远超本方案范围，留待后续单独提案。）

设计文档中**产品专有**（不引入）：solve plan 卡片、研究报告草稿、认知学徒制学习者注解、
KB 权限过滤、源排除重答——文档原文即写明"这是 DeepMentor 区别于通用 chat 产品的根本点"。

## 二、KAGWeb 现状缺口（已核对）

- **trace**：只有按 `call_id` 分组的平铺行 + 折叠/自动展开 + `describeToolCall`；无因果段、
  无回放、无一级摘要、无 insight 徽章、无 observation 层级、无 per-call token
  （只有 turn 级 `cost_summary`）。
- **DAG**：dagre + 紧凑时间线、DSL/Mermaid 导出导入、搜索；**无语义缩放、无思维导图导出**。
- **ask_user**：卡片只在 `tool_result` 携带 `tool_metadata.ask_user` 后出现；
  `extractMessageSegments` 已支持 text/ask_user/trace 交织；无 draft 概念。KAGWeb
  **没有注册 ask_user 工具**（`kagweb/tools/builtin_specs.py` 里没有它），但**提示词契约
  已经写好且处于未接线状态**：`kagweb/tools/prompting/hints/{en,zh}/ask_user.yaml`
  （`load_prompt_hints` 由工具注册表调用，没有注册的工具就没人读它）。因此该能力目前
  只能由外部后端按契约产出。
- **turn metadata**：assistant 行只写 `provider_response_state`；**无 turn insight**；
  store 协议**无 `update_message_metadata`**（`messages` 表已有 `metadata_json`）。
- **后端**：`StreamEventType.OBSERVATION` 与 `StreamBus.observation()` 已存在但**无生产者**；
  无 `runtime/agentic` 包。
- **测试**：`session_dsl_expected.json` + `generate_parity.mts` 存在，但**没有任何 Python
  测试消费该 fixture**——"DAG 与内联 trace 永不分歧"这条不变量目前没有自动守卫。

## 三、分阶段方案

> 阶段编号不代表严格串行：Phase 0 与 Phase 6 与其它阶段无依赖，可随时插入。
> 粗估工作量按"改动文件数 × 回归面"给量级（S ≤ 半天，M ≈ 1-2 天，L > 2 天）。

### Phase 0 — 通用 web 修复（建议最先发，S）

- **目标**：修掉线上可见问题，且与后续阶段零耦合。
- **改动**：`web/lib/markdown-tag-escape.ts` + `MarkdownRenderer.tsx`（模型回答里的
  XML 风格片段会让 React 19 直接崩溃——"The tag `<surface>` is unrecognized"；
  照搬 DeepMentor `eab0465e`）；`components/common/Tooltip.tsx` 的 pointer-leave blur
  （照搬；先确认 KAGWeb 是否也有 `components/ui/Tooltip.tsx` 重复文件再决定是否删除）。
- **测试**：照搬 `markdown-tag-escape.test.ts`、`common-tooltip-focus.spec.tsx`。
- **风险**：极低。修的是真实崩溃，建议单独提交、单独发布。

### Phase 1 — 分类内核统一 + DSL 平价锁（M，建议随后做）

- **目标**：无用户可见变化。分类规则收敛成一处，DAG/内联 trace/DSL 三方不再可能分歧，
  并补上缺失的跨语言平价测试。
- **改动**：
  - `web/features/chat/trace/selectors.ts`：新增 `TraceGroupClass` +
    `classifyTraceGroup(events)`（从 `DeepMentor/web/features/chat/trace/selectors.ts:170-221`
    移植），**并保留 KAGWeb 的未打标签容错子句**（`!kind && !group && 含 tool_call` 判为
    工具行，见 `TracePresentation.tsx:979`、`aggregate.ts:98`、`dsl_export.py:180`）。
  - 消费方改为调用内核：`selectors.ts::selectTraceDisplayItems`、
    `dag/aggregate.ts::walkCallGroups`、`TracePresentation.tsx::isToolRow`。
  - `kagweb/services/session/dsl_export.py::_walk_call_groups`：抽出
    `_classify_trace_group`，docstring 指明 TS 侧同名内核（Python 不能 import TS，
    镜像即契约）。
  - 新增 `tests/services/session/test_dsl_parity.py`：读
    `fixtures/session_dsl_expected.json`，用
    `build_session_dsl(..., stable=True, normalize_ids=True)` 对比
    `expected.stable_normalized`，并比对 `.mmd`。**不要比 `expected.raw`**
    （含 `exported_at`）。
- **测试**：`web/tests/trace-selectors.test.ts` 补内核用例（跳过规则 ×3、tool→retrieve→round
  优先级、subagent 去重、未打标签容错）；`tests/services/session/test_dsl_export_call_groups.py`
  补优先级与 subagent；`web/tests/session-dag.test.ts` 保持绿。
- **风险**：低。唯一雷区是照抄 DeepMentor 内核时丢掉容错子句——已有两个测试会抓。

### Phase 2 — Turn insight（后端 judge + 前端徽章，必须同一批）（L）

- **目标**：多轮回合结束后，活动头下方出现一行 takeaway + 类型徽章，刷新后仍在。
- **后端**：
  - `kagweb/services/session/protocol.py` 加 `update_message_metadata`；
    `sqlite_store.py`（`messages` 表已有 `metadata_json`，参考
    `_update_session_title_sync`）、`pocketbase_store.py`（写 dict 而非 `json.dumps`，
    对齐 KAGWeb 的 create 约定）各实现一份。
  - `turns/title_service.py` 加 `_maybe_generate_turn_insight`（移植
    `DeepMentor/.../title_service.py:163-285`，提示词去掉"学习助手"措辞，加
    `has_configured_llm()` 守卫）。
  - `turns/executor.py` 加 `_ROUND_CALL_KINDS` / `_count_llm_rounds` /
    `_ordered_tool_names`，在**已有的 post-DONE 槽**（`executor.py:559-578`，与标题生成
    同处）调用。`_count_llm_rounds` 数的是**不同 call_id**：KAGWeb 的桥接层只发
    `call_kind="agent_loop_round"`（从不发 `llm_final_response`），所以带工具的回合 ≥2
    出徽章、纯聊天回合 =1 不出、只有工具没有正文的回合 =0 不出。
  - **开关**：judge 是每回合一次额外 LLM 调用。加一个设置项（默认开），并用
    `has_configured_llm()`（`kagweb/services/llm/config.py:277`）守卫，未配置模型时直接跳过。
  - **存储容错**：`sqlite_store.py` 里 `metadata_json` 有两种默认值——`:245` 是 `'{}'`、
    `:294` 是 `''`。写入前必须按 dict / str 双向容错解析，否则老库上 `json.loads('')` 会抛。
  - **历史会话不回填**：老会话没有 `metadata.turn_insight`，徽章不显示；方案明确不写迁移。
- **前端**：新增 `web/lib/turn-insight.ts`（照搬）；`ChatStateAdapter.tsx` 加 `TurnInsight`、
  `MessageItem.turnInsight`、`TURN_INSIGHT` action、`hydrateTurnInsight`，并在
  `handleRunnerEvent` 里**先于 trace reducer** 拦截 `trace_kind === "turn_insight"`
  （该事件无 `call_id`，否则会被 `groupTraceEvents` 丢弃）；`ChatMessageList.tsx` 透传；
  `TracePresentation.tsx` 在 `AssistantActivity` 渲染徽章；两份 locale 加 5 个类型词。
- **测试**：移植 `tests/services/session/test_turn_insight_helpers.py`；新增
  `web/tests/turn-insight.test.ts`；回归 `test_turn_runtime_title.py`、`test_sqlite_store.py`。
- **风险**：全案最高。judge 是每个多轮回合多一次 LLM 调用（失败静默）；它与标题共享
  post-DONE 槽，需确认 socket 保持足够久让实时事件到达（executor 已为此延迟断开）。
  纯聊天回合 `round_count=1` → 不出徽章，符合预期。
- **顺序**：不依赖其它阶段；Phase 3 依赖它。

### Phase 3 — DAG 语义缩放 + 思维导图导出（M）

- **目标**：DAG 缩放时在 全文卡 → 收获牌 → 印章骨架 三档间切换（带迟滞）；工具栏一键导出
  **纯结构 SVG**（构造上不含任何对话文本）。
- **改动**：新增 `web/lib/dag-zoom.ts`、`web/lib/thought-map-export.ts`（均照搬）；
  `dag/model.ts` 加 `DagNodeMeta.turnInsight`；`dag/aggregate.ts` 透传；
  `dag/CytoscapeDag.tsx` 加 `.plaque`/`.glyph` 样式与 `cy.on("zoom")` 批量改类；
  `dag/SessionDagPanel.tsx` 加 `handleExportThoughtMap`（最长路径分层）+ 工具栏按钮；
  locale 加按钮文案。
- **测试**：照搬 `dag-zoom.test.ts`、`thought-map-export.test.ts`（零文本泄漏断言是核心保证）；
  回归 `session-dag.test.ts`。
- **风险**：低-中。cytoscape `zoom` 高频触发，必须保留"档位守卫 + `cy.batch`"。
  导出要走**同一套节点预算**：DAG 已有 `DAG_NODE_LIMIT = 2000`（`dag/model.ts:96-100`），
  超限时导出应与面板一致地截断并在 SVG 里标注，而不是导出一张不完整的图。
- **依赖**：Phase 2（节点要带 `turnInsight`）。

### Phase 4 — 三级披露：一级摘要行 + observation 通道 + 学习者/专家模式（M-L）

- **目标**：回合结束后原本"消失"的 trace 位置出现一行暗色摘要
  （`5 轮 · 3 次工具调用 · 2 个来源`）；折叠的工具行能看到机械截断的 observation 摘要；
  学习者/专家开关控制思考与参数密度。
- **前端**：新增 `web/lib/trace-summary.ts`、`web/lib/trace-mode.ts`、
  `web/hooks/useTraceMode.ts`、`web/tests/trace-settle.test.ts`（移植自
  `DeepMentor/web/lib/trace-summary.ts:28-203` 等）；`TracePresentation.tsx` 加
  `TraceTierOne`、折叠行的 observation 摘要、"模型自述"标记；`ChatWorkspace.tsx` 加
  trace 模式按钮；locale 加文案。
  - **落点必须按 DeepMentor 的优先级**：`detail = chip?.text ?? observationExcerpt ?? …`
    （`DeepMentor/web/features/chat/trace/TracePresentation.tsx:1105-1109`）。observation
    摘要只是**没有 chip 时的兜底**；照"折叠行显示 observation"直接实现会把工具行的
    命令/参数 chip 挤掉，是视觉回归。
  - **移植 `trace-mode.ts` 时要带上上游后来修的 localStorage 边界 bug**——DeepMentor
    的 `cot-visualization-rollout-impl.md` §"存量测试失败修复" 记录过这是真实缺陷
    （直接访问 `window.localStorage` 越界），不是"既有失败"。
- **后端**：`_AgentLoopRoundBridge._tool_result` 里用 `stream.observation(...)` 发 200 字
  机械截断的工具结果（**是截断，不是摘要**），metadata 复用
  `self._tool_trace(call_id, name, "observation", state)`。仅在结果文本非空时发——
  这会让每个 `tool_result` 多一条事件、`StreamBus` 历史翻倍，长回合的渲染成本要一起评估。
- **不引入**：`collectReplayBoundaries`/`ReplayPanel`（无回放）、
  `trace-struggle.ts`/`trace-metrics.ts`（需要后端 `notice` 键与埋点，KAGWeb 都没有）。
- **测试**：新增 `trace-settle.test.ts`；扩展 `web/tests/agent-loop-trace.spec.tsx`
  断言 CLI 回合出现 Observe 摘要；回归 trace 端点测试。
- **风险**：中。observation 是新渲染面，需确认不会漏进回答气泡
  （`shouldAppendEventContent` 只认 `content`）。
- **依赖**：Phase 1。

### Phase 5 — 每次调用耗时 / Token 元数据（DSL v1.1）（M）

- **目标**：专家模式 DAG 节点与 DSL 导出携带后端权威的 `duration_ms` 与每轮
  `tokens{prompt,completion,total}`；缺失时退回现有时间戳推算/null。
- **关键发现**：`usage` 事件**已经在 KAGWeb 产生**（`cli_backend.py:253-261` Claude
  `result`、`:403-416` Codex `token_count`），但 `_AgentLoopRoundBridge.forward` 没有
  `usage` 分支，所以 trace 拿不到——缺的是**挂载点**而非生产者。
- **改动**：`capability.py` 桥接层：`_tool_call`/`_tool_result` 之间记 `time.monotonic()`
  → `elapsed_ms`；`_run_single_pass` 把 pass usage 交给 `bridge.finish()`，在
  `_close_round` 的 complete 标记上归一化 `input/output → prompt/completion/total`；
  `dag/aggregate.ts::extractDurationMs` 优先 `elapsed_ms`；`dag/dsl.ts` 加 `tokens`；
  `dsl_export.py` 加 `_extract_tokens`/`_extract_duration_ms`；**扩展生成器输入并重新生成**
  `session_dsl_expected.json` + `.mmd`（TS 与 Python 必须同一提交移动）。
  - **只重跑生成器不够**：`generate_parity.mts` 的输入是手写的
    （`tests/services/session/fixtures/generate_parity.mts:42-60`），必须同时给它加
    `tokens`/`elapsed_ms` 样例，否则新字段在 fixture 里根本不出现、平价测试覆盖不到。
  - **DSL 版本策略要二选一**：`dsl.ts:23` 是 `DSL_VERSION = 1`，导入端 `dsl.ts:399`
    严格校验版本相等。要么保持 v1 只加可选字段（导入端忽略未知键），要么升 v2 并让
    导入端接受 v1。默认取前者（附加字段、向后兼容），实施时在 PR 说明里写明。
- **风险**：中-高（数据真实性）。CLI 的 usage 是 pass 级/累计（Codex 报的是
  `total_token_usage`），必须字段可选、绝不合成、并标注 `usage_scope`。
- **依赖**：Phase 1（平价测试）、Phase 4（桥接层改动相邻）。

### Phase 6 — ask_user 生命周期 + 通用 web 修复（M）

- **目标**：ask_user 卡片能在参数流式过程中就地生长（契约 + 渲染就绪，等后端产出）；
  提交被拒时给 toast + 重取会话，而不是卡片卡在"发送中"；中文答案里的 `**强调**`
  贴 CJK 标点时落库后仍能正确渲染。
- **改动**：
  - `AskUserOptions.tsx` 加 `ask_user_draft` 段（扫描/就地更新/被正式卡片取代）、
    `ChatMessageList.tsx` 加只读 draft 渲染与 `askCardsInteractive`、
    `ChatStateAdapter.tsx` 加 `ChatState.status`、`UnifiedTurnClient.ts` 加第 4 个构造
    参数 `onCommandRejected`（现有 3 个：`onEvent`/`onClose`/`onProtocolError`）、
    `ChatWorkspace.tsx` 透传。
  - 后端：`_turn_runtime_shared.py` 移植 `_repair_chinese_emphasis_*`（照搬，按
    `language.startswith("zh")` 生效），`executor.py` 在落库前套用。
  - 同时落地通用修复：`web/lib/markdown-tag-escape.ts` + `MarkdownRenderer.tsx`
    （React 19 未知标签崩溃，**建议先单独发**）、`components/common/Tooltip.tsx`
    pointer-leave blur。
- **适配**：拒绝处理体不能照抄（KAGWeb 没有 submit watchdog）——改为 `notify(error)` +
  `loadSession(sessionId)` 重取 + 复用 `hasPendingAskUserInMessages` 收敛卡片。
- **风险**：低。唯一注意：draft 渲染**目前没有生产者**，会是惰性代码；在
  `AskUserOptions.tsx` 写明契约，并加一个喂合成 `progress` 事件的测试把路径跑起来。
- **依赖**：无。

## 四、明确不引入

| DeepMentor 项 | 不引入的理由 |
|---|---|
| solve plan 卡片 / replan diff | 绑定 solve 能力，KAGWeb 没有 |
| 研究报告草稿 / 研究阶段文案 | 绑定 deep_research |
| 认知学徒制学习者注解、回放打包成"回看解题思路" | 文档原文：DeepMentor 区别于通用 chat 产品的根本点 |
| KB 权限过滤、源排除重答、可编辑计划 | 单租户，会话即鉴权边界；且属产品专有 |
| 困难时刻（struggle moments）、trace 埋点 | 需要后端 `notice` 键与指标设施，KAGWeb 均无 |
| `TurnMetadataService` 改名 | 纯改名 churn，KAGWeb 用 `SessionTitleService` 即可 |

## 五、排序与风险

- **Phase 0 最先**（S，与所有阶段无依赖）：修的是线上可见崩溃与交互缺陷，先发先受益。
- **Phase 1 次之**：它是 Phase 4/5 的硬依赖，零产品风险，且补上目前完全缺失的跨语言
  平价测试——分类规则现在有**三份手工维护的拷贝**，已经漂移过一次（KAGWeb 加了容错子句，
  DeepMentor 内核没有）。
- **Phase 2 与 3 必须按序**：徽章依赖 insight 数据；两者都动 `aggregate.ts`/`model.ts`。
  两者可同一版本发布，但仍是两个独立可回滚的提交。
- **Phase 2、4、6 都碰 `TracePresentation.tsx`**，按 2 → 3 → 4 → 6 排，保持每次 diff 可读。
- **Phase 5 最后**：字段全可选，CLI 家族可能拿不到 token；在平价测试存在之前不要加
  DSL 字段。
- 若以演示价值优先，可先做 Phase 2（独立且最可见），但若计划做 Phase 5，它不应早于
  平价测试落地。

## 六、验证方式

每个阶段收尾统一跑：

```bash
cd web && npm run test:node && npx vitest run && npm run typecheck && npm run lint && npm run i18n:check && npm run architecture:check
.venv/Scripts/python.exe -m pytest tests/services/session tests/api -q
```

端到端（Phase 2/4 完成后）：`.venv/Scripts/kagweb.exe start --dev` → 在
http://localhost:8092/chat 发一轮带工具调用的话 → 断言活动头出现 insight 徽章、
折叠态出现一级摘要行、展开后工具行带 observation 摘要。Phase 5 另跑
`npx tsx tests/services/session/fixtures/generate_parity.mts` 确认 fixture 与两端一致。

## 七、实施约定

1. 每个 Phase 单独分支 + 独立验证，不混批。
2. Phase 2（后端 judge + 前端徽章）必须同一提交落地，否则写进去的 metadata 没人读。
3. Phase 5 改 DSL 字段时，TS 与 Python 的 fixture 必须在同一提交内移动。

## 八、评审记录（2026-09-09，一轮）

评审方法：逐条核对方案里的 `file:line` 断言（对照两个仓库源码），与 DeepMentor 实际实现
比对，并做设计冲突检查（落点、优先级、兼容性）。

### 必改（本轮已改）

| # | 问题 | 处理 |
|---|---|---|
| R1 | "KAGWeb 没有 ask_user 工具实现" 不准确：提示词契约其实已经写好（`kagweb/tools/prompting/hints/{en,zh}/ask_user.yaml`），只是没注册成工具、无人读取 | §二 改写 |
| R2 | Phase 4 的 observation 摘要落点未定义。DeepMentor 的规则是 `detail = chip?.text ?? observationExcerpt ?? …`；按"折叠行显示 observation"字面实现会把工具行的命令/参数 chip 挤掉，属视觉回归 | Phase 4 写明优先级 |
| R3 | Phase 5 "重新生成 fixture" 不够：`generate_parity.mts` 的输入是手写的，只重跑不会让 `tokens`/`elapsed_ms` 出现在 fixture 里 | Phase 5 补"扩展生成器输入" |
| R4 | "引用悬停溯源" 被列进通用可引入清单，但六个阶段无一覆盖 | 移入"不排期"并说明前置条件 |

### 建议（本轮已采纳）

| # | 问题 | 处理 |
|---|---|---|
| R5 | markdown 标签转义放在 Phase 6，却又写"建议先单独发"——自相矛盾 | 提为 Phase 0 |
| R6 | Phase 2 每回合多一次 LLM 调用，没有开关与成本边界 | 加设置项 + `has_configured_llm()` 守卫 |
| R7 | Phase 2 store 写入未考虑 `metadata_json` 两种默认值（`'{}'` 与 `''`） | 写明 dict/str 双向容错 |
| R8 | 未说明历史会话是否回填 insight | 写明不回填 |
| R9 | Phase 5 未定 DSL 版本策略（`dsl.ts:23`，导入端 `:399` 严格校验版本相等） | 给出二选一，默认保持 v1 加可选字段 |
| R10 | Phase 4 移植 `trace-mode.ts` 未提上游修过的 localStorage 边界缺陷 | 已写明要带上修复 |
| R11 | Phase 4 的 observation 生产者会让每个 tool_result 多一条事件、`StreamBus` 历史翻倍 | 写明"仅非空时发"并要求评估渲染成本 |
| R12 | Phase 3 导出未说明超过 `DAG_NODE_LIMIT` 时的行为 | 写明按同一预算截断并在 SVG 标注 |
| R13 | 全案无工作量量级，无法排期 | 每阶段加 S/M/L 标注 |

### 保留不改（附理由）

| # | 建议 | 不采纳的理由 |
|---|---|---|
| R14 | 把 Phase 2 与 3 合并成"insight 端到端"一次交付 | 保留两阶段：Phase 3 可独立回滚，且 DAG 改动面与后端 judge 无耦合 |
| R15 | 把 Phase 4/5 的桥接层改动合并 | 保留分开：Phase 4 是渲染面、Phase 5 是数据面，合并后单次回归面过大 |
| R16 | 给出人日估算 | 仓库没有历史速率数据，S/M/L 是当前能给的最可靠量级 |

### 复核结论

方案主体成立。排序在评审后由"Phase 1 优先"调整为 **Phase 0 → Phase 1**（先修线上可见
缺陷，再做零风险的内核统一）。必改项已全部落到正文，无遗留阻塞项。
