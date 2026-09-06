# Chat Session 思维链 DAG 视图（v2，已评审修订）

## Context

Chat 界面当前已能对**单个 turn**渲染思维链 trace（`AssistantActivity`/`TraceFlow`，见 `web/features/chat/trace/TracePresentation.tsx`），但整个会话（含所有历史 turn）没有任何"总览图"。用户希望以 **DAG 图**形式、覆盖**整个 Chat Session**，查看从用户提问到最终回答的完整思考/工具调用链。

探索结论（约束）：
- 后端 trace 事件元数据里**没有显式父子/DAG 边字段**，层次需从 `metadata.call_id`（==trace_id，同调用相等）、`trace_group`(stage/tool_call/retrieve)、顺序号 `iteration_index/tool_index/consult_index`、消息分叉 `parent_message_id` 推导。
- Web 端事件按 **per-turn**（`MessageItem.events`）存储于 `ChatStateAdapter` 的 reducer 状态，`state.messages` 即含所有历史 turn 的完整事件，**无需新增后端 API** 即可重建整个会话 DAG。
- 前端 `package.json` 已声明 `cytoscape@^3.33.1`（lock 已解析），但**无任何代码 import 它**，且 `node_modules` 当前未安装。

决策（用户已确认）：
- 渲染引擎：**cytoscape** 原生图（内置 breadthfirst 布局，无新运行时依赖）。
- 节点粒度：**turn/调用层，可下钻**（默认每条 assistant 消息一个节点，点开下钻到内部轮次/工具/检索）。
- 首个版本**纯前端实现**，不改后端协议，不破坏现有 per-turn 的 trace 渲染。

## 评审修正记录（v2）

| # | 严重度 | 问题 | 修正 |
|---|---|---|---|
| 1 | 高 | 原 i18n 步骤有乱码行 | 重写为明确对照表（见步骤 7） |
| 2 | 高 | cytoscape 无捆绑 TS 类型、未装 `@types/cytoscape`，typecheck 会失败 | 新增 devDependency `@types/cytoscape`；"零新增依赖"修正为"零新增**运行时**依赖"；验证前先 `npm install` |
| 3 | 高 | retrieve 挂载只用指针不精确 | 判据改为 call_id 匹配：复用工具 call_id 的 retrieve 挂该工具下；独立 call_id 的 KB 预取 seed 直接挂消息/轮次下 |
| 4 | 中 | `MessageItem.id?` 可选（乐观占位无 id） | 节点 id 用 `id ?? index` 兜底；`expandedMessages` key 同理 |
| 5 | 中 | cytoscape UMD 包顶层 import 有 SSR 风险 | `CytoscapeDag.tsx` 在 useEffect 内动态 `import("cytoscape")` |
| 6 | 中 | 流式期间全量重算+重排抖动 | 面板打开时才计算/挂载；流式期间对 dag 输入做 ~500ms debounce（仅展示层，不影响数据正确性） |
| 7 | 低 | useMemo 原放 ChatWorkspace，面板关闭时也在算 | 计算挪进 `SessionDagPanel` 内部；ChatWorkspace 只加按钮 + open 状态 |
| 8 | 低 | `MessageLike` 来源未定义 | 直接 `import type { MessageItem } from "@/features/chat/ChatStateAdapter"`（已 export） |
| 9 | 低 | `Network` 图标未在 import 列表 | ChatWorkspace 补 lucide-react import |
| 10 | 中 | DAG 面板与 Activity 面板（`SessionViewerPanel`）同层，同时打开会互相叠盖 | 复用现有互斥模式（`FilePreviewDrawer` 先例）：两侧面板互斥，详见步骤 6 |

第三轮评审（#17–#22，基于代码核实 `state.messages` 分支语义与 IR 字段完备性）：

| # | 严重度 | 发现 | 修正 |
|---|---|---|---|
| 17 | **高（V1）** | `state.messages` 是服务器扁平全量列表（含全部编辑分支），可见路径由 `buildVisiblePath` 过滤；aggregate 直接吃全量列表会把分支错误串成一条线 | aggregate 输入先过 `buildVisiblePath`（复用 `web/lib/message-branches.ts`），见步骤 2 |
| 18 | 中（V2，波及 V1 model） | DSL 草案字段（query/duration_ms/error/text_preview/consult_index/round）不在 V1 IR meta 中，"V1 零改动"结论不准确 | V1 `model.ts` meta 增补 6 个展示字段，聚合时从 events 提取（见步骤 1/2） |
| 19 | 中（V2） | `parseSessionDsl` 无法完整还原 IR（root/collapsed/childCount/edges 不在 DSL 中；normalizeIds 后 id 不可逆） | 快照测试语义定为"归一化树比较"（AST 级），不承诺 IR 全等 |
| 20 | 中（V2） | 流式 turn 导出语义未定义（state 可为 running，含半成品） | 导出如实含 running 节点并标记；`stable:true` 快照模式仅用于已结束会话 |
| 21 | 中（V2） | `text_preview` 可能泄用户敏感内容，白名单管不住 preview 本身 | preview 截断 140 字符（对齐 `plainPreview`）+ `includeText` 导出选项（默认 true） |
| 22 | 低 | 乐观消息（负 id）会出现在可见路径上（rank offset 1e15 机制） | DAG 如实反映 UI 可见状态，仅注明；导出 DSL 时可过滤未持久化消息（负 id） |

不纳入 V1（记录为后续增强）：deep_research 跨两 turn 的 mergedEvents 合并展示；`cytoscape-dagre` 更优布局；DSL 执行链导出（详见文末"V2 演进"章节）。

## 目标产物

在 Chat 头部按钮区新增"会话思维链"入口，点击打开右侧抽屉面板（对齐现有 `SessionViewerPanel` 的 `open/onClose` 手势），用 cytoscape 渲染整段对话的 DAG：消息链为主干，可逐条 assistant 消息下钻展开其内部调用链。

## 复用与依赖决策

- 复用 `web/features/chat/trace/selectors.ts` 低层工具：`getTraceMeta`、`getTraceGroup`、`getTraceCallKind`、`getCallProvider`、`groupTraceEvents`；类型 `TraceMetadata`/`TraceItem` 来自 `trace/model.ts`。
- 标签/着色复用 `web/lib/trace-tools.ts` 的 `describeProviderTool`、`getToolProvider`（与 `TracePresentation` 一致）。
- 面板视觉复用 `web/components/activity/`（`ActivityDetailGrid` 等）与 `--primary`/CSS 变量配色语言。
- 依赖：**零新增运行时依赖**；新增 1 个 devDependency `@types/cytoscape`（cytoscape 官方包不带 .d.ts）。布局用 cytoscape 内置 `breadthfirst`（directed, roots=[root]），`cytoscape-dagre` 留作 v2。

## 实现步骤

### 1. 新建 `web/features/chat/dag/model.ts`
纯类型，不 import React/cytoscape：
- `DagNodeKind = "root" | "user" | "assistant" | "round" | "tool_call" | "retrieve" | "subagent"`
- `DagNode { id; kind; label; parentId; seq; meta { messageIndex; messageId?; messageRole?; capability?; callId?; callKind?; callState?; traceGroup?; toolName?; provider?; subagentName?; childCount; query?; error?; durationMs?; round?; consultIndex?; textPreview? }; collapsed }`（meta 后 6 个字段为 V2 DSL 预留，评审#18，聚合时从 events 提取）
- `DagEdge { id; source; target; kind: "conversation" | "drilldown" }`
- `SessionDag { nodes; edges; byId: Map<string, DagNode>; expandable: Set<string> }`（expandable 见细化#23）
- 节点 id 规则（保证唯一）：`msg:{messageId ?? "i"+index}`（乐观负 id 用 index 兜底，防非法 CSS selector，细化#25）、`call:{callId}:{kind}:{seq}`（同类调用共享 call_id 时用 kind+seq 消歧）。
- 实现级细化（类型全量、算法伪代码、边界表、测试矩阵）见 [chat-session-dag-impl.md](./chat-session-dag-impl.md)。

### 2. 新建 `web/features/chat/dag/aggregate.ts`
纯函数（不 import cytoscape，便于测试与回滚）：
```ts
import type { MessageItem } from "@/features/chat/ChatStateAdapter";
import { buildVisiblePath } from "@/lib/message-branches";
export function computeSessionDag(
  input: { messages: MessageItem[]; selectedBranches?: Record<string, number> },
  expandedMessages?: Set<string> | null,
): SessionDag
```
推导规则：
- **输入先过 `buildVisiblePath(messages, selectedBranches)`**（评审#17，复用 `web/lib/message-branches.ts` 现成函数）：`state.messages` 是服务器返回的**扁平全量列表**（含编辑产生的全部兄弟分支），不过滤会把所有分支错误串成一条线；可见路径才是 DAG 主干。乐观消息（负 id）会出现在路径上（rank offset 机制，评审#22），DAG 如实反映 UI 可见状态。
- 保持可见路径顺序；消息内事件先按事件顺序（数组序，已按 seq 递增）分组，组内再用数值化的 `iteration_index/tool_index/consult_index` 做稳定排序。
- 虚拟 `root`；每条消息 → user/assistant 节点，顺序边 `msg[i]→msg[i+1]`（kind=conversation）；分支点由 `buildVisiblePath` 的 `siblingsByMessageId` 提供 `childCount`（总分支数）挂到父 user 节点。完整分支树（非选中分支的 trace）不在 V1 DAG 展示，留给 V2 DSL `branches`。
- assistant 节点默认 `collapsed=true`（不在 expandedMessages 时只 1 个节点，childCount=隐藏子节点数）。
- 展开时 materialize（线性扫描 call 分组，维护 currentRound/currentTool 指针；**分组行为**：`groupTraceEvents` 按 call_id 分组、Map 首现序 = 发生序）：
  - `trace_group=="stage"` / `call_kind ∈ {agent_loop_round, llm_planning, llm_generation}` → `round` 节点，边 消息→round；
  - `trace_group=="tool_call"` → `tool_call` 节点，边 currentRound??消息→tool；**组内扫描**（细化#26，修正评审#3）：复用工具 call_id 的 retrieve/subagent 事件被合并进该工具组而非独立组 → distinct `(subagent_name, consult_index)` 生成 `subagent` 子节点挂该工具下，retrieve 的 query 并入工具节点 meta；
  - `trace_group=="retrieve"` 的**独立组**（仅 KB 预取 seed 有独立 call_id）→ `retrieve` 节点挂 currentTool??currentRound??消息 下；
  - `call_kind=="llm_final_response"` → 终结标记挂消息节点（不单独成节点，与 `selectors.ts` skip 逻辑一致）。
- 折叠态对**所有** assistant 消息预计算调用树规模（`countCallTree`，与 materialize 共用 `walkCallGroups` 私有收口，细化#28）→ `expandable` 集合 + childCount 角标（细化#23）。
- 聚合时**同时从 events 提取 DSL 所需的展示字段**（评审#18）存入节点 meta：`query`、`error`、`duration_ms`（事件 timestamp 首尾差）、`round`（轮次号）、`consult_index`、`text_preview`（content 截 140 字符）——V2 序列化器直接读 IR，无需回查事件。
- 输出同时填 `byId` 与每节点 `collapsed`/`childCount`；`expandedMessages` 的 key 用 `msg:{messageId ?? index}`（评审#4）。

### 3. 新建测试 fixture builder + 单元测试
- `web/tests/session-dag-fixtures.ts`：导出 `ev(type, overrides)`、`userMsg(id, content)`、`assistantMsg(id, opts)` 构造器（放 `web/tests/` 下避免进页面 bundle）。
- `web/tests/session-dag.test.ts`：`node:test` + `node:assert/strict`（同 `trace-selectors.test.ts` 风格；`npm run test:node` 自动收集，vitest 只收 `*.spec.ts` 不冲突）。
- 覆盖用例：空输入→仅 root；user→assistant(无事件)消息链；stage 轮次+多工具+检索（共享 call_id 消歧、层级父边、seq 顺序）；**两种 retrieve（复用工具 call_id vs 独立 call_id）的挂载差异**；subagent 挂载；乱序事件稳定排序；展开/折叠节点计数；**分支会话：多兄弟分支 + selectedBranches 时仅选中路径入图**（评审#17 核心回归项）；**无 id 消息的 index 兜底**；meta 预留字段提取（query/duration/round 等，评审#18）。
- 不复用 `web/tests/fixtures/provider-trace-events.json`（扁平序列、无 call_id）。

### 4. 新建 `web/features/chat/dag/CytoscapeDag.tsx`
- `"use client"`；props `{ dag; onToggleMessage(nodeKey); selectedNode?; onSelectNode }`。
- **动态加载**（评审#5）：`useEffect` 内 `const cytoscape = (await import("cytoscape")).default` 再建实例，避免 SSR 阶段执行 UMD 包；卸载时 `cy.destroy()`。
- 依据 `dag` 构建/替换 elements；样式：root/user/assistant/tool_call/retrieve/subagent 用现有 CSS 变量着色；collapsed assistant 节点加 "+N" 角标；conversation 边实线、drilldown 边虚线 bezier。
- 交互 state 驱动：`cy.on("tap")` 点 assistant 节点 → `onToggleMessage(key)` → 面板重算 dag → 新 elements 喂回。缩放/拖拽原生 + `cy.fit()` 按钮。
- 点选节点用 `ActivityDetailGrid` 显示详情。
- **流式节流**（评审#6）：对 `dag` 变更做 ~500ms debounce 后再重建/重排（展示层缓解，不改数据）。

### 5. 新建 `web/features/chat/dag/SessionDagPanel.tsx`
- 右侧抽屉外壳（对齐 `components/chat/home/SessionViewerPanel` 的 open/onClose 手势），渲染位置与 `FilePreviewDrawer`/`SessionViewerPanel` 同级（ChatWorkspace 尾部面板层）。
- **DAG 计算放面板内部**（评审#7）：`useState(expandedMessages)` + `useMemo(() => computeSessionDag({messages, selectedBranches}, expandedMessages), [...])`；props `{ open; onClose; messages; selectedBranches }`。
- **open=false 时不挂载内容**（return null / 条件渲染），保证面板关闭时零计算开销。

### 6. 修改 `web/features/chat/components/ChatWorkspace.tsx`（仅新增）
- 顶部按钮区（约 line 2311 的 Activity 按钮之前）新增：
  ```tsx
  <HeaderActionButton onClick={() => setSessionDagOpen(v => !v)} active={sessionDagOpen}
    icon={Network} label={t("Session DAG")} title={t("View the whole conversation as a trace graph")} />
  ```
- 补 lucide-react `Network` import（评审#9）；加本地状态 `sessionDagOpen`。
- **与 Activity 面板互斥**（评审#10，复用现有 `FilePreviewDrawer` 互斥先例）：
  - `SessionViewerPanel` 的 open 条件追加 `&& !sessionDagOpen`（现约 line 2606 为 `open={viewerPanelOpen && previewSource === null}`）；
  - 打开 DAG 面板的回调置 `setSessionDagOpen(true)` 即可（Activity 面板因 open 条件自动收起），关闭 DAG 面板不主动恢复 Activity（与 FilePreviewDrawer 关闭后行为一致，由用户自行再开）。
- 渲染 `<SessionDagPanel open={sessionDagOpen} onClose={...} messages={state.messages} selectedBranches={state.selectedBranches} />`，位置与 `FilePreviewDrawer`/`SessionViewerPanel` 同级（尾部面板层，约 line 2599-2612 处插入）。不改现有聊天列/滚动容器/`ChatMessageList` 调用。

### 7. i18n：修改 `web/locales/en/app.json` + `web/locales/zh/app.json`
新增成对 key（先 grep 确认无重复再添加）：

| en key/value | zh value |
|---|---|
| `"Session DAG"` | `"会话思维链"` |
| `"View the whole conversation as a trace graph"` | `"以图查看整个会话的思维链"` |
| `"Round"` | `"轮次"` |
| `"Tool call"` | `"工具调用"` |
| `"Retrieval"` | `"检索"` |
| `"Subagent"` | `"子代理"` |
| `"Expand"` / `"Collapse"` | `"展开"` / `"折叠"` |
| `"{{n}} hidden calls"` | `"{{n}} 次隐藏调用"` |
| `"No trace events in this session yet"` | `"本会话暂无轨迹事件"` |

工具/检索 provider 标签走 `describeProviderTool` 返回串，不新增 key。

## 关键文件

- 新建：`web/features/chat/dag/model.ts`、`web/features/chat/dag/aggregate.ts`、`web/features/chat/dag/CytoscapeDag.tsx`、`web/features/chat/dag/SessionDagPanel.tsx`、`web/tests/session-dag.test.ts`、`web/tests/session-dag-fixtures.ts`
- 修改：`web/features/chat/components/ChatWorkspace.tsx`、`web/locales/en/app.json`、`web/locales/zh/app.json`、`web/package.json`（devDep `@types/cytoscape`）
- 复用（不改）：`web/features/chat/trace/{selectors,model}.ts`、`web/lib/trace-tools.ts`、`web/components/activity/`

## 验证

0. `cd web && npm install`（当前 node_modules 未安装；同时装上新增的 `@types/cytoscape`）。
1. `npm run test:node`（含新增 session-dag 单测）、`npm run typecheck && npm run lint && npm run i18n:check`。
2. `npm run dev` → 打开一个含工具调用 / deep_research / 多轮分支的会话 → 点击"会话思维链" → 确认 DAG 渲染、assistant 节点下钻展开/折叠正确、两种 retrieve 挂载正确。
3. 回归：确认原有每条消息下方 per-turn 思维链（`AssistantActivity`/`TraceFlow`）不受影响；面板关闭时无 DAG 计算；**面板互斥**——打开 DAG 时 Activity 面板自动收起，打开 Activity/文件预览时 DAG 面板正常关闭，三者不叠盖。
4. 边界：空会话显示空状态文案；流式期间面板打开图不持续跳动（debounce 生效）；大图可缩放拖拽。

## 风险与回滚

- 对现有 trace 渲染**零影响**：新代码只 import `trace/selectors.ts` 低层 helper 与 `trace/model.ts` 类型（只读、无副作用），不动 `TracePresentation.tsx` 渲染路径。
- 聚合纯函数完全解耦 cytoscape → 整体回滚 = 删除 `web/features/chat/dag/` + `web/tests/session-dag*` + ChatWorkspace 按钮 + 两个 locale 的 key + devDep。
- 无后端/API/协议改动。

## V2 演进：DSL 执行链（后续增强，不在 V1 范围）

### 与 DAG 的架构关系

`computeSessionDag` 产出的 `SessionDag`（nodes+edges）本质是**中间表示（IR）**。DAG 与 DSL 是同一 IR 的两个投影：

```
state.messages[].events
        │  computeSessionDag()（V1 已实现，纯函数）
        ▼
    SessionDag (IR)
      │                       │
      ▼                       ▼
CytoscapeDag.tsx          serializeSessionDsl()（V2 新增，纯函数）
视觉视图：交互下钻           结构化 JSON：存、比、查、传
```

因此 DSL 的边际成本低：V1 已把推导逻辑收口在聚合层，V2 只加一个序列化器 + 入口（面板内"导出 DSL"按钮 / `deepmentor` CLI 子命令），DAG 侧零改动。

### DSL 相比 DAG 的新增功能

| 能力 | DAG（视觉视图） | DSL（结构化文本） |
|---|---|---|
| 交互探索（点击下钻/缩放） | ✅ | ❌ |
| 导出/分享/归档（贴进 issue、报告、notebook） | ❌ | ✅ |
| 会话间 diff（对比两次执行的思维链：重试了哪些调用、轮次增减） | ❌ | ✅ |
| 检索/统计（grep/jq 查询、工具调用频次、失败链定位） | ❌ | ✅ |
| 回归测试基线（trace 结构快照测试） | ❌ | ✅ |
| 离线渲染（DSL → Mermaid codegen，进 CI/报告，复用现有 `components/Mermaid.tsx` 管线） | ❌ | ✅ |
| 后端演进桥接（若后端补 `parent_call_id`，DSL 作为稳定交换格式） | — | ✅ |

一句话：**DAG 是"看"，DSL 是"存、比、查、传"**。二者互补而非替代。

### DSL JSON 结构草案（v1 规范形态）

设计原则：**树形嵌套**（而非扁平 nodes+edges）作为规范形态——文本可读、diff 友好；`node` 保留 IR 节点 id 保证可往返；**白名单字段**序列化，永不携带原始 args/`tool_metadata`（防泄密、防大 payload）。

```json
{
  "version": 1,
  "generator": "deepmentor/1.6.4",
  "session": {
    "id": "unified_1725_xxxx",
    "exported_at": "2026-09-03T10:00:00Z",
    "turn_count": 8
  },
  "trace": [
    {
      "node": "msg:3",
      "kind": "user",
      "text_preview": "Explain Fourier transform"
    },
    {
      "node": "msg:4",
      "kind": "assistant",
      "capability": "chat",
      "state": "complete",
      "duration_ms": 8420,
      "calls": [
        {
          "node": "call:chat-responding-a1b2c3d4e5:round:0",
          "kind": "round",
          "label": "Round 1",
          "state": "complete",
          "calls": [
            {
              "node": "call:chat-loop-0-tool-0-f6a7b8c9d0:tool_call:0",
              "kind": "tool_call",
              "tool": "web_search",
              "provider": "duckduckgo",
              "label": "Searching the web",
              "state": "complete",
              "duration_ms": 1200,
              "calls": [
                {
                  "node": "call:chat-loop-0-tool-0-f6a7b8c9d0:retrieve:0",
                  "kind": "retrieve",
                  "provider": "duckduckgo",
                  "query": "fourier transform",
                  "state": "complete"
                }
              ]
            },
            {
              "node": "call:chat-loop-1-tool-0-1a2b3c4d5e:tool_call:0",
              "kind": "tool_call",
              "tool": "consult_subagent",
              "state": "complete",
              "calls": [
                {
                  "node": "call:chat-loop-1-tool-0-1a2b3c4d5e:subagent:0",
                  "kind": "subagent",
                  "subagent_name": "math-helper",
                  "consult_index": 1,
                  "state": "complete"
                }
              ]
            }
          ]
        }
      ]
    }
  ]
}
```

消息分叉（`parent_message_id`）用递归 `branches` 表达：

```json
{
  "node": "msg:9",
  "kind": "user",
  "text_preview": "…",
  "branches": [
    { "selected": true,  "trace": [ { "node": "msg:10", "kind": "assistant" } ] },
    { "selected": false, "trace": [ { "node": "msg:12", "kind": "assistant" } ] }
  ]
}
```

字段规范：

| 层 | 字段 | 说明 |
|---|---|---|
| 根 | `version` / `generator` / `session` | 版本演进 + 来源追溯 |
| 消息层 | `node` / `kind`(user\|assistant) / `text_preview` / `capability` / `state` / `duration_ms` | `node` == IR 节点 id，保证往返 |
| 分支 | `branches[].selected` + 递归 `trace` | 对应 `selectedBranches` 折叠语义 |
| 调用层 | `node` / `kind`(round\|tool_call\|retrieve\|subagent) / `label` / `state` / `tool` / `provider` / `query` / `subagent_name` / `consult_index` / `duration_ms` / `error` | kind 枚举与 V1 `DagNodeKind` 完全一致 |
| 嵌套 | 统一 `calls` | 树形嵌套隐含 drilldown 边；数组顺序隐含 conversation 边 |

### 基于草案的方案评审（补充 #11–#16）

| # | 严重度 | 发现 | 修正 |
|---|---|---|---|
| 11 | 中 | V2 原写"缩进树文本/YAML"，与确定的 JSON 规范形态不一致 | DSL 规范形态定为 JSON；缩进文本/YAML 仅作可选渲染输出 |
| 12 | 中 | 跨会话 diff 不可用：`node` 含 uuid，两次执行 id 必然不同 | 序列化器加 `normalizeIds` 选项：重编号 `turn:1`/`call:1.2` 顺序 id，diff 只比结构 |
| 13 | 中 | `duration_ms`/`error` 属易变字段，默认导出污染 diff 基线 | 加 `stable` 选项（默认 false）：为 true 时剔除易变字段，用于快照测试 |
| 14 | 高 | 安全：若直接序列化 IR meta，`tool_metadata`/args 可能带密钥或大 payload | 采用白名单字段序列化（仅上表字段），永不透传 IR meta 原文 |
| 15 | 低 | V1 `childCount` 等字段与 DSL 无对应 | 无需改动：childCount 是 UI 折叠角标专用，DSL 从嵌套结构天然可重推导 |
| 16 | 低 | `label` 是 i18n 解析后的串，序列化时依赖 `t` | DSL 以结构化字段（tool/provider/query）为主，`label` 可选；消费方可自行本地化 |

对 V1 的反向影响（第三轮评审后修正）：① V1 `model.ts` meta 增补 6 个展示字段（query/error/durationMs/round/consultIndex/textPreview），聚合时从 events 提取（评审#18）；② aggregate 输入先过 `buildVisiblePath`（评审#17，属正确性修复）。kind 枚举已对齐，除此之外无其他 V1 改动。

### V2 落地要点（供后续排期）

- 新增 `web/features/chat/dag/dsl.ts`：`serializeSessionDsl(input, opts?): string`（**输入为 `SessionDagInput` 而非已折叠的 dag**，内部按 `expandAll` 全展开计算，细化#33/#34；白名单字段 JSON，选项 `stable`/`normalizeIds`/`includeText`，见 #13/#12/#21）与 `parseSessionDsl(text)`（用于归一化树校验，**不承诺 IR 全等**，见 #19）。
- 面板头部加"导出 DSL"按钮（下载 .json，或复制到剪贴板）；复用 ChatWorkspace 现有 `handleDownloadMarkdown` 的下载模式。导出含 running 节点并如实标记（#20）；默认过滤负 id 未持久化消息（#22）。
- 快照测试：`web/tests/session-dag-dsl.test.ts` 断言 IR → DSL(`stable:true`) → 归一化树往返一致（AST 级比较，非 IR 全等），作为能力改动的行为回归护栏。
- CLI 侧（可选）：`deepmentor session trace <id> --format dsl` 直接从 SQLite `turn_events` 重建导出，后端复用同一推导规则（需把 `aggregate.ts` 规则在 Python 侧重写或通过前端导出）。
