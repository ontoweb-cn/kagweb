# Chat Session 思维链 DAG/DSL 细化设计（模块级，含技术评审）

> 主方案见 [chat-session-dag.md](./chat-session-dag.md)（三轮评审 #1–#22）。本文档把方案细化到可直接落码的模块设计，每个模块附细化评审（编号延续 #23 起）。细化中推翻/修正了主方案的两处结论，见汇总表。

## 模块 1：`web/features/chat/dag/model.ts`

```ts
import type { StreamEvent } from "@/features/chat/model/protocol";

export type DagNodeKind =
  | "root" | "user" | "assistant"
  | "round" | "tool_call" | "retrieve" | "subagent";

/** 消息分叉摘要（来自 buildVisiblePath.siblingsByMessageId）。 */
export interface DagBranchInfo {
  total: number;   // 分支总数（含当前）
  index: number;   // 当前分支 1 基序号
}

export interface DagNodeMeta {
  // —— 消息层 ——
  messageIndex: number;            // 可见路径中的序号（0 基）
  messageId?: number;              // 服务器 id；乐观消息为负，缺失时无
  messageRole?: "user" | "assistant";
  branchInfo?: DagBranchInfo;      // 仅分支点消息有
  capability?: string;
  // —— 调用层 ——
  callId?: string;
  callKind?: string;
  callState?: string;              // running | complete | error
  traceGroup?: string;
  toolName?: string;
  provider?: string;               // getCallProvider 结果
  // —— 调用层展示字段（DSL 预留，评审#18）——
  query?: string;
  subagentName?: string;
  consultIndex?: number;
  roundIndex?: number;
  durationMs?: number;
  textPreview?: string;            // content/plainPreview 截 140 字符
  error?: string;
  // —— UI ——
  childCount: number;              // 折叠时隐藏的子节点数
}

export interface DagNode {
  id: string;                      // "root" | "msg:{id|i{idx}}" | "call:{callId}:{kind}:{n}"
  kind: DagNodeKind;
  parentId: string | null;         // 语义父（消息或调用容器）
  seq: number;                     // 同父内 0 基序号
  meta: DagNodeMeta;
}

export interface DagEdge {
  id: string;                      // "e:{source}->{target}"
  source: string;
  target: string;
  kind: "conversation" | "drilldown";
}

export interface SessionDag {
  nodes: DagNode[];
  edges: DagEdge[];
  byId: Map<string, DagNode>;
  /** 有可下钻内容的 assistant 消息节点 id 集合（折叠态也预计算 childCount）。 */
  expandable: Set<string>;
}
```

**细化评审（模块 1）**

| # | 结论 |
|---|---|
| #23 | **新增 `expandable` 集合**：主方案只输出 nodes/edges，折叠消息的调用树不 materialize，但 `childCount`（"+N"角标）与"哪些节点可点开"必须在折叠态可知 → 聚合时对**所有** assistant 消息预算调用树（只计数不产出节点），`expandable`/`childCount` 由预计算填充。成本：每消息一次轻量分组，可接受。 |
| #24 | `roundIndex` 替代主方案的 `round`：selectors 无轮次号（后端不写 metadata.round），细化为"该消息内 stage/tool 组的 0 基序号"，语义自洽。 |
| #25 | 节点 id 对乐观消息（负 id）用 `msg:i{index}` 而非 `msg:-1725…`：index 在一次计算内稳定，且避免负号进入 cytoscape selector（`#msg:-1` 是非法 CSS id selector）。 |

## 模块 2：`web/features/chat/dag/aggregate.ts`

```ts
import type { MessageItem } from "@/features/chat/ChatStateAdapter";
import type { SessionDag, DagNode, DagNodeKind } from "./model";

export interface SessionDagInput {
  messages: MessageItem[];
  selectedBranches?: Record<string, number>;
}

export function computeSessionDag(
  input: SessionDagInput,
  expandedMessages?: Set<string> | null,
): SessionDag;

/** 仅统计调用树规模（折叠角标用），不产出调用节点。导出复用（模块 8）。 */
export function countCallTree(message: MessageItem): number;
```

### 算法（伪代码）

```
computeSessionDag(input, expanded):
  visible = buildVisiblePath(input.messages, input.selectedBranches)
  nodes = [root]; edges = []; expandable = ∅
  prev = "root"

  for msg, i in visible.messages:            # 已滤 system（hydrateMessages 保证，防御见#26）
    key = msg.id != null ? `msg:${msg.id}` : `msg:i${i}`
    meta = { messageIndex: i, messageId: msg.id, messageRole: msg.role,
             capability: msg.capability,
             branchInfo: visible.siblingsByMessageId.get(msg.id) }   # id 缺失则无
    if msg.role == "assistant" and msg.events 有 trace 实质:
      meta.childCount = countCallTree(msg)
      if childCount > 0: expandable.add(key)
    nodes.push({ id: key, kind: msg.role, parentId: prev, seq: i, meta })
    edges.push({ source: prev, target: key, kind: "conversation" })
    if key ∈ expanded and expandable.has(key):
      materializeCalls(msg, key)             # 生成 drilldown 节点
    prev = key

  return { nodes, edges, byId: Map(nodes), expandable }
```

```
countCallTree(msg):                            # 与 materializeCalls 同一遍分组逻辑
  groups = groupTraceEvents(msg.events)        # 复用 trace/selectors，组序=首现序=发生序
  count = 0; lastTool = null
  for g in groups:
    group = firstTraceGroup(g)                 # 组内第一个非空 trace_group
    if group == "stage" or callKind(g) ∈ {agent_loop_round, llm_planning, llm_generation}:
      if callKind(g) == "llm_final_response": continue    # 终结标记，不计数（#主方案步骤2）
      count += 1; lastTool = null              # round 节点
    elif group == "tool_call":
      count += 1; lastTool = g.callId          # tool 节点
      count += 组内 distinct (subagentName, consultIndex) 数    # 组内子节点（#23）
    elif group == "retrieve":                  # 独立 call_id = KB 预取 seed
      count += 1
  return count
```

```
materializeCalls(msg, msgKey):
  groups = groupTraceEvents(msg.events)
  seq = 0; currentRound = null; currentTool = null
  for g in groups:
    group = firstTraceGroup(g); kind = classify(g)      # round|tool_call|retrieve
    if kind == round:
      if callKind(g) == "llm_final_response": continue
      nodeId = `call:${g.callId}:round:${n_round++}`
      parent = msgKey; currentRound = nodeId; currentTool = null
    elif kind == tool_call:
      nodeId = `call:${g.callId}:tool_call:${n_tool++}`
      parent = currentRound ?? msgKey; currentTool = nodeId
    else:  # retrieve（独立 call_id）
      nodeId = `call:${g.callId}:retrieve:${n_ret++}`
      parent = currentTool ?? currentRound ?? msgKey    # KB seed 通常无先行 round → msgKey
    nodes.push(makeNode(nodeId, kind, parent, seq++, extractMeta(g)))
    edges.push({ source: parent, target: nodeId, kind: "drilldown" })
    # tool 组内：subagent 子节点 + query 提取（#23）
    if kind == tool_call:
      for (name, ci) in distinct 组内 (subagent_name, consult_index):
        subId = `call:${g.callId}:subagent:${n_sub++}`
        nodes.push(...); edges.push(tool节点 -> subId, drilldown)
```

`extractMeta(g)` 从组内事件提取：`callState`（`call_status` 的 `call_state`）、`provider`（`getCallProvider`）、`toolName`（tool_call 事件）、`query`（meta.query）、`durationMs`（首末事件 timestamp 差，`getTurnDurationSeconds` 思路）、`error`（error 事件 content 截断）、`textPreview`（thinking/content 首段截 140）。

### 边界与防御

| 输入 | 行为 |
|---|---|
| messages 空 | 仅 root，无边 |
| assistant 无 events / 仅 final_response | 节点产出但不可展开（不进 expandable） |
| 事件无 call_id | `groupTraceEvents` 丢弃（既有语义，保持一致） |
| 组内 trace_group 全空 | kind 兜底 round（与 `getTraceGroup` 空串语义一致，取 callKind 判断） |
| cycle / 重复 call_id | Map 分组天然合并；id 用 kind+自增序号保证唯一 |

**细化评审（模块 2）**

| # | 严重度 | 发现 | 修正 |
|---|---|---|---|
| #26 | 高 | **主方案评审#3 的 retrieve 判据不成立**：`groupTraceEvents` 按 call_id 分组，rag 工具内部派生的 retrieve 事件**复用工具 call_id → 合并进该工具组**，永远不产生独立 retrieve 组；独立 retrieve 组只有 KB 预取 seed（独立 call_id）。同理 subagent 事件复用 call_id 也进工具组。 | 改为**组内扫描**：tool 组内 distinct `(subagent_name, consult_index)` 生成 subagent 子节点；retrieve 元数据并入 tool 节点 meta.query。主方案步骤 2 的"call_id 相同→挂工具下"分支删除（不存在该形态）。 |
| #27 | 中 | `visible.siblingsByMessageId` 按**消息 id**索引，乐观消息（负 id）也有 entry；但 `msg.id == null` 的消息取不到 → branchInfo 缺失。 | 可接受：`id == null` 仅出现在极短的乐观窗口，branchInfo 为 undefined 即可；测试覆盖 id 存在路径。 |
| #28 | 低 | countCallTree 与 materializeCalls 双份分组逻辑易漂移。 | 收口为私有函数 `walkCallGroups(events)` 产出统一分组描述（kind/序号/subagent 列表/meta），count 与 materialize 都消费它；测试锁定两函数一致性（计数 == 展开节点数）。 |
| #29 | 低 | deep_research 两 turn 合并（ChatMessageList mergedEvents）不在 V1 范围，DAG 会把 turn-1/turn-2 显示为两个 assistant 节点。 | 如实展示，与主方案"不纳入 V1"一致；文档注明。 |

## 模块 3：`web/features/chat/dag/CytoscapeDag.tsx`

```tsx
"use client";
export interface CytoscapeDagProps {
  dag: SessionDag;
  onToggleMessage: (nodeKey: string) => void;
  selectedNode: string | null;
  onSelectNode: (id: string | null) => void;
}
```

实现要点：
- `useEffect(() => {...}, [dag])`：动态 `const cytoscape = (await import("cytoscape")).default`（首次加载后模块缓存）；建实例 → `applyElements` → `runLayout` → `cy.fit(undefined, 40)`。dag 变更时**复用实例**：`cy.elements().remove(); cy.add(elements); runLayout()`（避免整图重建闪烁）。卸载 `cy.destroy()`。
- 元素映射：node → `{ data: { id, label: displayLabel(node), kind, state } }`；edge → `{ data: { id, source, target, kind } }`。label：assistant = `A{index}` + (branchInfo ? ` {index}/{total}` : "")；user = `Q{index}`；tool_call = toolName/provider；round = `R{n}`；retrieve/subagent 同理。**label 由面板传入 `t` 后生成的 map**（组件不直接依赖 i18n，见 #31）。
- 样式（cytoscape style 数组，颜色取 CSS 变量的**计算值**——cytoscape 不能直接用 `var(--x)`，初始化时 `getComputedStyle(document.documentElement).getPropertyValue(...)` 解析一次）：
  - 节点：`shape: "round-rectangle"`，`border-width: 1`，按 `data(kind)` 着色（user: `--muted` 底；assistant: `--primary` 边 + `--primary/12` 底；tool_call/retrieve/round/subagent: 小号字 `--muted-foreground`）。
  - 折叠 assistant：label 后缀 `+N`（`data(childCount)`，由映射时拼接）。
  - 边：`conversation` 实线 `line-color: var(--border)`；`drilldown` 虚线 `line-style: "dashed"` + `curve-style: "bezier"`。
- 布局：`{ name: "breadthfirst", directed: true, roots: ["root"], spacingFactor: 1.3, nodeDimensionsIncludeLabels: true, padding: 40, animate: false }`（animate:false 换稳定，#30）。
- 交互：`cy.on("tap", "node", ...)`——`kind == "assistant" && expandable` → `onToggleMessage(id)`；否则 `onSelectNode(id)`；点空白 → `onSelectNode(null)`。
- **debounce（主方案#6）**：dag 引用变更后 500ms 再 applyElements；用 `useRef` 持最新 dag，定时器 cleanup。
- 容器尺寸变化（面板打开动画）：`ResizeObserver` → `cy.resize(); cy.fit()`。

**细化评审（模块 3）**

| # | 严重度 | 发现 | 修正 |
|---|---|---|---|
| #30 | 中 | cytoscape CSS 变量问题：style 数组不解析 `var(--x)`，主题切换/SSR 首帧取不到值。 | 初始化时从 `getComputedStyle` 读一次 hex 值并缓存；主题切换场景 V1 不做（面板生命周期短），文档注明。 |
| #31 | 中 | 组件内 `useTranslation` 会让纯渲染组件耦合 i18n 且 label 随语言变化触发全图重排。 | label 在**面板层**生成：`SessionDagPanel` 用 `t` 把 dag → `Map<nodeId, label>` 传入；CytoscapeDag 保持纯 props。 |
| #32 | 低 | `cy.elements().remove() + add` 在流式高频更新时仍整图 layout。 | debounce 已缓解；另加 `elements diff`：同 id 节点只更新 `data`，仅增删差异节点（V1 先做 remove+add，diff 留优化）。 |

## 模块 4：`web/features/chat/dag/SessionDagPanel.tsx`

```tsx
"use client";
export interface SessionDagPanelProps {
  open: boolean;
  onClose: () => void;
  messages: MessageItem[];
  selectedBranches?: Record<string, number>;
}
```

- 外壳：`fixed right-0 top-0 z-[30] h-dvh w-[min(720px,92vw)] border-l bg-[var(--card)]` + `transition-transform`（对齐 SessionViewerPanel line 646 模式）。
- state：`expandedMessages: Set<string>`、`selectedNode: string | null`。
- `dag = useMemo(() => computeSessionDag({messages, selectedBranches}, expandedMessages), [messages, selectedBranches, expandedMessages])`；**open=false 时 return null**（主方案#7）。
- `labels = useMemo(() => buildLabels(dag, t), [dag, t])`（#31）。
- 头部：标题 + "Fit view"（`cy.fit()` 经 ref 暴露）+ 关闭按钮。空态：`!dag.expandable.size` 显示 "No trace events in this session yet"。
- 底部详情条（`selectedNode` 非空时）：`ActivityDetailGrid`，rows 按 kind 映射（tool → tool/provider/query/state；round → capability/state；subagent → name/consultIndex；消息 → role/capability/childCount）。

**细化评审（模块 4）**：无新问题。`open=false return null` 与 transition-transform 动画冲突（无卸载动画）——V1 接受瞬时收起（FilePreviewDrawer 同款行为）。

## 模块 5：ChatWorkspace 接线（精确 diff）

1. import：`Network`（lucide-react 已有 import 组）、`SessionDagPanel`。
2. `const [sessionDagOpen, setSessionDagOpen] = useState(false);`
3. 头部按钮（Activity 按钮之前，约 line 2311）：
```tsx
<HeaderActionButton onClick={() => setSessionDagOpen(v => !v)} active={sessionDagOpen}
  icon={Network} label={t("Session DAG")} title={t("View the whole conversation as a trace graph")} />
```
4. `SessionViewerPanel` open 条件追加互斥（line 2606）：`open={viewerPanelOpen && previewSource === null && !sessionDagOpen}`。
5. 面板层插入（line 2612 后）：
```tsx
<SessionDagPanel open={sessionDagOpen} onClose={() => setSessionDagOpen(false)}
  messages={state.messages} selectedBranches={state.selectedBranches} />
```

**细化评审（模块 5）**：无新问题。`state.selectedBranches` 经 context 暴露（ChatStateAdapter line 141），确认可达。

## 模块 6：i18n（en/zh app.json 成对新增）

`"Session DAG"`/`"会话思维链"`、`"View the whole conversation as a trace graph"`/`"以图查看整个会话的思维链"`、`"Fit view"`/`"适配视图"`、`"{{n}} hidden calls"`/`"{{n}} 次隐藏调用"`、`"No trace events in this session yet"`/`"本会话暂无轨迹事件"`、`"Round"`/`"轮次"`、`"Tool call"`/`"工具调用"`、`"Retrieval"`/`"检索"`、`"Subagent"`/`"子代理"`、`"State"`/`"状态"`、`"Query"`/`"查询"`、`"Provider"`/`"提供方"`。（"Expand"/"Collapse" 不需要——下钻即点击节点。）

**细化评审（模块 6）**：`npm run i18n:check` 做 parity+audit，重复 key 会报错 → 添加前 `grep` 现有 key（"Round"/"Query"/"Provider" 大概率已存在于 trace 相关文案），复用不新增。

## 模块 7：测试

`web/tests/session-dag-fixtures.ts`：
```ts
export function ev(type: StreamEvent["type"], meta?: Partial<Record<string, unknown>>,
  content?: string, timestamp?: number): StreamEvent;
export function userMsg(id: number | null, content: string): MessageItem;
export function assistantMsg(id: number | null, events: StreamEvent[]): MessageItem;
```

`web/tests/session-dag.test.ts`（node:test）用例矩阵：

| 用例 | 断言 |
|---|---|
| 空输入 | 仅 root |
| user→assistant(无事件) | 2 节点 + root，1 conversation 边，不可展开 |
| 单消息 1 round + 2 tool | 折叠：childCount==3；展开：round→tool×2 层级边正确 |
| tool 组内 2 个 subagent 事件（同 call_id） | 2 个 subagent 子节点挂 tool 下（#26 回归） |
| 独立 retrieve 组（KB seed，独立 call_id） | retrieve 节点挂消息下 |
| **rag retrieve 事件复用 tool call_id** | **无独立 retrieve 节点**，query 并入 tool meta（#26 回归） |
| 事件乱序（call_id 交错） | 组序=首现序，节点 seq 稳定 |
| 分支会话（2 兄弟 + selectedBranches） | 仅选中路径入图，branchInfo 正确（#17 回归） |
| 乐观消息（负 id / null id） | 节点 id `msg:-3` / `msg:i2`，无崩溃（#25） |
| countCallTree == 展开节点数 | 一致性锁定（#28） |

`web/tests/session-dag-dsl.test.ts`（V2，见模块 8）。

## 模块 8：`web/features/chat/dag/dsl.ts`（V2）

```ts
import type { SessionDagInput } from "./aggregate";

export interface SerializeDslOptions {
  stable?: boolean;        // 剔除易变字段（durationMs/error/session/exported_at）
  normalizeIds?: boolean;  // node id 重编号 turn:N / call:N.M
  includeText?: boolean;   // 是否含 textPreview（默认 true）
}

/** 全展开计算 + 序列化。输入原始 input 而非已折叠 dag（见 #33）。 */
export function serializeSessionDsl(
  input: SessionDagInput, opts?: SerializeDslOptions): string;

/** 归一化树解析（校验用，不承诺还原 SessionDag）。 */
export function parseSessionDsl(text: string): DslDocument;
```

实现要点：
- **输入是 `SessionDagInput` 而非 `SessionDag`**：导出需要**全展开**调用树，而面板的 dag 只展开了用户点开的消息 → 序列化器内部调 `computeSessionDag(input, ALL)`（全展开标记 = expandable 全集）。
- IR（flat）→ DSL（树）：从 root 沿 `conversation` 边得消息序；每消息节点取其 `drilldown` 出边子树递归。
- 白名单映射：`DagNodeMeta` → DSL 字段一一对应（模块 1 已对齐），`includeText:false` 剔 `text_preview`；`stable:true` 剔 `duration_ms`/`error` + 根部 `session`/`exported_at`；`normalizeIds:true` 前序遍历重编号。
- 负 id 乐观消息默认过滤（`messageId != null && messageId < 0` 跳过）。
- 快照测试：`serialize(stable, normalizeIds)` 双向（序列两次比较）+ `parse` 后树结构断言。

**细化评审（模块 8）**

| # | 严重度 | 发现 | 修正 |
|---|---|---|
| #33 | 高 | 主方案 V2 签名 `serializeSessionDag(dag)` 有误：面板 dag 是**用户折叠视角**，导出会丢失未点开消息的调用树。 | 签名改为 `serializeSessionDsl(input, opts)`，内部全展开计算（本模块已改）。 |
| #34 | 中 | `computeSessionDag(input, ALL)` 需要"全展开"语义，但 expandable 集合要先算一次才能全展开 → 两遍计算。 | 接受两遍计算（导出是低频操作）；或 aggregate 加 `expandAll?: boolean` 第三参短路第一遍。V1 实现 `expandAll` 参数。 |
| #35 | 低 | branches 递归（草案）需要全量消息（含未选中分支），但 `SessionDagInput.messages` 就是全量列表 → 可直接在 dsl.ts 内自行构树，不过 buildVisiblePath。 | branches 结构 V2 实现时直接从 `input.messages` 的 parentMessageId 构树，不经 IR。 |

## 细化评审汇总（#23–#35）

| 级别 | 项 |
|---|---|
| 推翻主方案 | **#26**（retrieve/subagent 复用 call_id 合并进组，判据改为组内扫描）、**#33**（DSL 序列化器输入改为 input+全展开） |
| 高 | #23（expandable 集合）、#26、#33 |
| 中 | #27（乐观消息 branchInfo）、#28（walkCallGroups 收口）、#30（CSS 变量）、#31（label 面板层生成）、#34（expandAll 参数） |
| 低 | #25（负 id selector）、#29（deep_research 两节点如实展示）、#32（元素 diff 留优化）、#35（branches 直构） |

主方案文档需同步修订：步骤 2 的 call_id retrieve 判据（#26）、V2 签名（#33）。

## V1 实施评审记录（#36–#45，编码中逐模块评审）

| # | 严重度 | 发现 | 处置 |
|---|---|---|---|
| 36 | 事实修正 | `buildVisiblePath` 跳过 `id === undefined` 的消息 → 可见路径消息必有 id | 节点 key：正 id 用 `msg:{id}`，负/缺失 id 用 `msg:i{index}`（CSS selector 安全），`messageNodeKey()` 收口 |
| 37 | 事实修正 | `TraceMetadata.round` 字段已存在 | `roundIndex` 优先取 meta.round，缺失用消息内 0 基序号 |
| 38 | **高（正确性）** | `TracePresentation` 的规范分类：retrieve 判定用 `trace_role`（非 trace_group）；tool 含 `kind === "tool_planning"`；且跳过 `llm_final_response`、`absorbed_into_final`、无实质组（`groupHasTraceSubstance`） | `walkCallGroups` 完全对齐该规范，DAG 与内联 trace 视图永不分歧 |
| 39 | 中（架构） | 从 `ChatStateAdapter.tsx`（React 组件文件）import 类型会引入耦合 | 改为模块内最小结构类型 `DagMessage`（同 `BranchMessage` 模式），architecture:check 验证通过 |
| 40 | 事实修正 | cytoscape 已在 dependencies（非新增）；仅缺 `@types/cytoscape` | devDep 补齐；`node_modules` 已安装 |
| 41 | 低 | `@types/cytoscape` 样式属性类型混杂：节点尺寸类为 string、edge width/arrow-scale 为 number、`text-max-width` 为 string | 按 d.ts 逐属性对齐 |
| 42 | 低 | cytoscape 导出为 `StylesheetStyle`（非 Stylesheet/StylesheetCss） | 已修正 |
| 43 | 低 | 首版 CytoscapeDag 的 badge 判断逻辑混乱（`!has() === false`）+ 两处重复 layout 代码 | 重构：`applyToCanvas` 收口、`materialized` 集合判定 badge |
| 44 | 中（测试发现） | 测试 fixture 默认 `parentMessageId: null` 使 assistant 成 user 的兄弟节点而非子节点，暴露链式 fixture 需显式父指针 | 修 fixture；真实数据中服务器写 parent 指针，不受影响 |
| 45 | 复核 | i18n audit 报 118 个缺失 t() 字面量 | 全为存量技术债（"Branch" 等），本次 12 个 key 全部有词条，parity OK |

**V1 验证结果**：test:node 1025/1025（含新增 12 用例）；typecheck ✔；architecture:check ✔（730 模块零违规）；eslint ✔（仅存量 img 警告）；i18n parity ✔；contracts:check ✔；test:unit 22/22 ✔。

**交付文件**：`web/features/chat/dag/{model,aggregate,CytoscapeDag,SessionDagPanel}.tsx?`、`web/tests/{session-dag-fixtures.ts,session-dag.test.ts}`、ChatWorkspace 接线（按钮/互斥/挂载）、locales 12 key。

## V1 代码质量与安全评审（R1–R8，已全部修复）

安全结论：无可利用风险——label 走 canvas 渲染无 XSS 向量、详情值经 React 转义、用户内容仅 140 字符截断预览、不透传原始 args/`tool_metadata`、cytoscape 为本地 npm 依赖。

| # | 级别 | 问题 | 修复 |
|---|---|---|---|
| R1 | major | `open=false` 时 useMemo 仍全量重算 DAG（messages 随流事件高频变引用，面板关闭时白耗） | dag 计算加 `open` 门控返回 null，labels/childCounts/detailRows 空态兜底 |
| R2 | major | selectedNode 在重建 effect 依赖中 → 点击看详情触发整图 remove+add+layout+fit，重置用户视口 | selection 拆为独立 effect，仅 `cy.select()`，不重建不重排 |
| R3 | medium | `data.expandable` boolean vs 选择器 `"true"` 字符串：cytoscape `valCmp` 的 `=` 是严格相等（node_modules/cytoscape/src/selector/data.mjs L45-47），虚线样式永不生效 | toElements 序列化为字符串 `"true"/"false"` |
| R4 | medium | debounce 无 maxWait，持续流式（间隔<500ms）无限重置定时器，画布饥饿不更新 | 加 2s maxWait（`lastApplyRef` 计算剩余延迟） |
| R5 | minor | fitToken 在 cytoscape 异步 import 完成前触发则静默丢失 | `pendingFitRef` 记住意图，mount 完成后补执行 |
| R6 | minor | `open ? "translate-x-0" : "translate-x-full"` 死分支（early return 保证 open 恒真），面板无动画 | 删死分支，改 `animate-fade-in` 入场 |
| R7 | minor | durationMs 取数组首末 timestamp 差，乱序时为负/低估 | 改 min/max 遍历 |
| R8 | minor | render 期间写 `latestRef.current`，违反 React 并发渲染规则 | 移入同步 useEffect |

评审方式：双并行子代理交叉验证 + cytoscape 源码直接核实（R3）。修复后复验：typecheck ✔ / test:node 1025/1025 ✔ / eslint ✔ / architecture:check ✔。

## V2 DSL 实施记录（模块 8，#46–#48）

| # | 类型 | 发现 | 处置 |
|---|---|---|---|
| 46 | 实施 | `expandAll` 实现为 `computeSessionDag` 第三参 `ComputeDagOptions`，materialize 条件 `expandAll \|\| expanded.has(key)`，**单遍完成**全展开（优于 #34 预估的两遍计算） | [aggregate.ts](../../web/features/chat/dag/aggregate.ts) |
| 47 | 实施 | 树构建不走 IR edges 重建整树，而是 `messageCalls(msgKey)` 沿 drilldown 边递归；消息查找用 `nodeByIndex` Map（避免 O(N²) find） | dsl.ts |
| 48 | 测试发现 | 首轮 4 个 DSL 测试失败——**断言按 flat 结构写，序列化实际是正确的嵌套树**（tsx 直调源码证实），修正断言而非实现 | session-dag-dsl.test.ts |

落地与方案差异：
- branches 挂在**选中消息**条目上（`branch` + `branches` 字段），草案语义一致；分支子树经 `buildVisiblePath` 覆盖选择重算，深度 guard `MAX_BRANCH_DEPTH=8`
- `stable` 剔除 `duration_ms`/`error`/整个 `session` 块；`normalizeIds` 前序 DFS 重编号 `turn:N`/`turn:N.M`
- 导出按钮在 DAG 面板头部（Download 图标），Blob 下载 `session-dsl-{sessionId}.json`；`SessionDagPanel` 新增 `sessionId` prop

**V2 验证**：test:node 1034/1034（+9 DSL 用例：树形结构/全展开独立性/stable/normalizeIds/includeText/负 id 过滤/分支递归/parse 校验/字节级快照稳定）· typecheck ✔ · eslint ✔ · architecture:check ✔（731 模块）· i18n parity ✔。

**交付文件**：`web/features/chat/dag/dsl.ts`（序列化+解析）、aggregate `expandAll`、`SessionDagPanel` 导出按钮、`web/tests/session-dag-dsl.test.ts`、locales +2 key（Export DSL）。

## V2 DSL 代码质量与安全评审（D1–D5，已修复）

安全结论：无可利用风险——白名单序列化杜绝 metadata 透传（#14）、`includeText` 可关、sessionId 为服务器 uuid（文件名无注入面）、parse 仅 JSON 结构校验。

| # | 级别 | 问题 | 修复 |
|---|---|---|---|
| D1 | major | 分支递归组合爆炸：branch 路径内再遇 fork 继续收集 → M 个 fork × K 分支 = K^M 条路径，每条全量重算 expandAll DAG + buildVisiblePath 重复执行，重度编辑会话导出阻塞主线程 | branches 单层展开（`allowBranches` 仅顶层）；`subVisible` 传入递归复用 |
| D2 | medium | normalizeIds 下 branch 与主 trace 撞 id（convertTrace 每次新建 counters，branch 从 turn:1 重新编号） | branch 命名空间前缀 `b{K}:turn:N`（K 为 siblingIds 位置序号，选中变化时稳定） |
| D3 | minor | messageCalls 每节点 O(E) 全量扫边 → 整树 O(N×E) | `buildChildrenIndex` 每图一次 O(E) 建 Map 索引 |
| D5 | minor | dsl.ts 手写 `parentId == null ? "null" : ...` 重复 message-branches 的 parentKey（ROOT_KEY 变更会静默破坏 override） | message-branches 导出 `parentKey`，dsl 复用 |

排除（误报）：文件名 sessionId 注入——服务器 uuid + 浏览器 download 属性无路径穿越面。

新增回归测试 2 个：normalizeIds+branch 前缀命名空间、branch 单层展开语义（嵌套 fork 有 branch 元数据无 branches 数组）。修复后复验：test:node 1036/1036 ✔ · typecheck ✔ · eslint ✔ · architecture:check ✔（731 模块）。

**注意**：branches 语义从"递归嵌套（≤8 层）"收紧为"单层展开"——D1 的修复决策，DSL 文档中 branch 内的 fork 仅保留 `branch` 元数据。

## 模块 9：`web/features/chat/dag/dsl-mermaid.ts`（DSL → Mermaid codegen）

主方案 V2 功能表："离线渲染（DSL → Mermaid codegen，进 CI/报告，复用现有 `components/Mermaid.tsx` 管线）"。`Mermaid.tsx` 输入契约为 mermaid 文本字符串（L6-11），`securityLevel:"strict"`（L40-63）——本模块只产文本，渲染安全由现有管线负责。

设计：
- `dslToMermaid(doc: DslDocument): string` 纯函数，`flowchart TD`
- 消息链纵向：user → assistant → …；调用树在 assistant 下链式展开（assistant → round → tool → subagent，DFS 序）
- branch：fork 节点 `-.->` 虚线边（label `branch k`）指向 branch trace 首节点，branch 内部同构渲染
- dsl.ts 重构：抽出 `buildSessionDslDocument(input, opts): DslDocument`（serializeSessionDsl = stringify 它），面板 Mermaid 导出无需 parse 往返
- 面板头部加 "Export Mermaid" 按钮，下载 `session-dsl-{id}.mmd`

**细化评审（模块 9）**：
- #49（高）：DSL node id 含 `:`（`msg:1`/`turn:2.1`/`b1:turn:1`）——mermaid id 语法不允许，直接用会渲染失败 → sanitize（非 `[A-Za-z0-9_-]` 替 `_`）+ 已用集合去重防撞
- #50（中）：label 内 `"` 结束引号、`#` 是 mermaid 实体码前缀（`#quot;`）、换行断语法 → 全部清洗；label 截 48 字符（text_preview 140 太长，节点可读性优先）
- #51（低）：branch trace 的 raw id（msg:N）与主 trace 天然不重合（消息树无重入），但 normalize 模式前缀 `b1:` sanitize 后为 `b1_turn_1`，与主 trace `turn_1` 不撞——dedupe 集合兜底即可
- #52（低）：中文/Unicode label 在 mermaid 引号内合法——测试覆盖

## 模块 10：`kagweb/services/session/dsl_export.py` + CLI `session trace`

主方案 V2 落地要点第 4 条："`kagweb session trace <id> --format dsl` 直接从 SQLite 重建导出，后端复用同一推导规则"。

关键事实（已核实）：
- `messages` 表已内嵌 `events_json`（`_serialize_message` L1617-1631 返回 `events`/`parent_message_id`/`capability`），`get_session` 直接可得——**无需 join `turn_events`**（那是运行时 journal）
- CLI 模式：`session_cmd.py` 经 `KAGWebApp.get_session(session_id)` 取数（`_show_session` 同款）
- Python 测试落位 `tests/services/session/`（已有 test_sqlite_store.py 等）

设计：
- `build_session_dsl(messages, *, stable=False, normalize_ids=False, include_text=True) -> dict`：移植 web 侧推导（`groupTraceEvents` call_id 分组 → `walkCallGroups` 分类 → 可见路径 `buildVisiblePath` 端口 → 单层 branches）
- 分类规则逐条对齐：skip `llm_final_response`/`absorbed_into_final`/无实质组；`tool_planning|tool_call` → tool_call；`trace_role=retrieve` → retrieve；其余 round；subagent distinct `(name, consult_index)`；tool name/tool state/query/min-max duration/clip 140
- 可见路径端口：childrenByParent 按 id 升序（持久化行无负 id），默认选末位（最新）；branch info = 1-based 位置/总数/sibling ids/parent id；branch 前缀 `b{K}:`（K = siblingIds 位置，与 web D2 修复一致）
- CLI：`kagweb session trace <id> --format dsl [--stable] [--normalize-ids] [--text/--no-text] [--out FILE]`
- **跨语言 parity 测试**：TS 侧（tsx 直调）从 fixture 生成 `tests/services/session/fixtures/session_dsl_expected.json`（stable+normalizeIds），Python 测试从同款 fixture 构建并断言 JSON 全等——推导规则漂移的护栏

**细化评审（模块 10）**：
- #53（高）：跨语言移植最大风险是规则漂移（TS 改了 Python 不知道）→ parity 测试双向锁死；fixture JSON 由 TS 生成后提交，非手写
- #54（中）：`groupHasTraceSubstance` 的 content 判据依赖 `isNarrationRound`/`isChatLoopAnswerContent`（call_kind=agent_loop_round 的 content 不算实质）——必须完整移植，不能简化成"有事件就算"
- #55（低）：Python 侧持久化消息无负 id（乐观消息不落库），负 id 过滤规则可省略但保留防御无妨
- #56（低）：`session trace` 的 `--format` 仅支持 dsl（Mermaid codegen 不做 Python 端口——CLI 用户可经 DSL + web 侧管线渲染，避免双语言维护三份规则）

## 模块 9/10 实施记录（#57–#60）

| # | 类型 | 发现 | 处置 |
|---|---|---|---|
| 57 | 测试发现 | mermaid 测试首轮 3 失败：真实节点 id 带 kind+seq 后缀（`call:r1:round:0`）而断言按简写；label 长度算术漏了 "User: " 前缀；dedupe 用例中 `-` 是合法字符不产生碰撞（改用 `msg:1:1`） | 修正断言（tsx 直调源码证实实现正确） |
| 58 | 实施 | Python 移植首轮自查发现 `_convert_trace` 的 reuse 路径 siblings 来源错误（应取 branch 自身可见路径，而非无选择路径）+ 双 visible-path 函数重复 | 收口为单一 `_visible_path(messages, selected=None)`，reuse 携带 (visible, siblings, prefix) 三元组 |
| 59 | 测试发现 | raw id parity 差一位：TS raw id 内嵌 IR 物化 seq（0 基），Python DFS 计数 1 基（normalize 用）| raw 分支改用 `counters["call"] - 1`（DFS 前序 == 物化序，两模式各自正确）|
| 60 | 测试发现 | Python 单测重犯 #48（断言按 flat 写，DSL 实为嵌套树）| 修正为 `calls[0]["calls"][0]` 取嵌套 tool |

**模块 9 交付**：`web/features/chat/dag/dsl-mermaid.ts`（`dslToMermaid` 纯函数：id sanitize+dedupe、label 清洗截 48、branch 虚线边）、dsl.ts 抽出 `buildSessionDslDocument`、面板 "Export Mermaid" 按钮（下载 .mmd）、`web/tests/session-dag-mermaid.test.ts` 7 用例（结构/branch/清洗/截断/dedupe/空文档/Unicode）。

**模块 10 交付**：`kagweb/services/session/dsl_export.py`（`build_session_dsl`：分组/分类/实质过滤/嵌套规则/可见路径/单层 branch 全量移植）、CLI `kagweb session trace <id> [--format dsl] [--stable] [--normalize-ids] [--text/--no-text] [--out]`、parity fixture（tsx 生成 `tests/services/session/fixtures/session_dsl_expected.json`，含输入 + stable_normalized + raw 两期望）、`tests/services/session/test_dsl_export.py` 12 用例。

**验证**：web test:node 1043/1043 ✔ · typecheck ✔ · eslint ✔ · architecture:check ✔（732 模块）· i18n parity ✔；Python tests/cli + tests/services/session 341 passed ✔；**E2E**：fixture 种子入 SQLite → `kagweb session trace --stable --normalize-ids` 输出与 TS fixture 全等（已清理种子会话）。

## 模块 11–15 细化设计（V3 扩展，2026-09-03 圈定 D/E/A/B/F）

### 模块 11：DAG 面板 DSL 导入预览
面板头部新增 Import 按钮（Upload 图标）→ file picker → `parseSessionDsl` → **DSL 树直接构造 SessionDag IR**（不伪造 DagMessage、不走 computeSessionDag）：trace 顺序连 conversation 边，calls 递归连 drilldown 边，branches 用 sibling 边 + 独立子链。面板进入 "imported" 模式：banner 显示文件名 + 返回会话视图按钮；详情条复用 ActivityDetailGrid（DSL 字段反向映射回 meta：tool→toolName、state→callState、query/subagent_name/consult_index/round_index/duration_ms/error 直映）。
- **#61（评审）**：安全——导入内容只驱动渲染（无 eval/网络/持久化），`parseSessionDsl` 已做版本+形状校验；加 10MB 文件大小护栏防大文件卡顿。i18n 新 key：Import DSL / Imported DSL view / Back to session。
- **#62（评审）**：不反向构造 DagMessage 再跑聚合（messageIndex/可见路径语义只对真实会话有意义）；直接构造 IR，节点 id 用 DSL node 字段原值（导入文档不参与负 id CSS 规则，但 sanitize 仍走 messageNodeKey 兜底）。

### 模块 12：deep_research 跨 turn mergedEvents 合并
对齐 ChatMessageList.tsx L1379-1410 的配对判据：assistant + capability=deep_research + result.metadata.outline_preview + 下一个 deep_research assistant 的 `isConfirmedResearchFollowup` → 父消息 events 追加 followup events、followup 节点从 DAG/DSL 中删除。
- **#63（评审，高）**：合并必须在**可见路径之后**应用（ChatMessageList 即如此）——原始列表上合并可能把不同分支的 parent/followup 错配。落地：aggregate 导出 `visibleMessagesForDag(input)`（buildVisiblePath + mergeDeepResearchPairs），computeSessionDag 与 dsl.ts convertTrace **共用**，meta.messageIndex 以合并后列表为准，杜绝索引漂移。
- **#64（评审，高）**：三处一致性——chat 列表、DAG、DSL 导出（web+Python）必须同一合并视图；Python `dsl_export.py` 同步移植 mergeDeepResearchPairs（配对判据/顺序一致），parity fixture **重新生成**并加入 deep_research 配对用例锁死。

### 模块 13：cytoscape-dagre 层次布局
新增运行时依赖 `cytoscape-dagre`（+其依赖 dagre）；在 CytoscapeDag 现有动态 import 块内注册扩展。布局配置：`{ name: "dagre", rankDir: "TB", nodeSep: 40, edgeSep: 12, rankSep: 60 }`。面板头部加布局切换按钮（默认 dagre，可切回 breadthfirst）。
- **#65（评审）**：主方案"零新增运行时依赖"仅约束 V1；dagre 属明文"留作 v2"项，可加。CJS interop 风险：cytoscape-dagre 是 CJS，需核实 next.config alias/动态 import 的 interop 形态（cytoscape 本体已走 CJS alias，先例可循）。fit 请求/debounce 逻辑不动。

### 模块 14：Python dsl_to_mermaid + CLI --format mermaid
`dsl_export.py` 增加 `dsl_to_mermaid(doc)`：逐行为移植 web dsl-mermaid.ts（id sanitize 正则/label 清洗/LABEL_LIMIT=48/branch 虚线边/输出尾换行）。
- **#66（评审）**：与 TS **字节级 parity**——fixture 由 tsx 生成期望 .mmd，Python 测试断言全等（sanitize 正则与 join("\n")+"\n" 必须逐字符对齐）；推翻 #56"不做 Python 端口"的决策（用户圈定 E，理由：CLI 用户无需 web 即可出图）。

### 模块 15：DSL diff
CLI `kagweb session diff <a.json> <b.json>`：对两份导出的 DSL 文档做结构 diff。配对策略：按 trace 位置索引配对（同长度精确对位；不同长度时前 min(n) 对位、多出/缺失整段报 added/removed）；每对 entry 比较 kind/capability/text_preview + calls 树（按 (kind, tool, round_index) 指纹多集比较）报 tool 增删；`--json` 输出机器可读摘要，默认 rich 表格。
- **#67（评审）**：不做 LCS 对齐（过度工程）——文档限制：位置配对在两份导出插入/删除中间轮次时会错位，建议配合 --normalize-ids + --stable 导出使用；此限制写入 --help。纯读文件操作，无 store 依赖。

## V3 实施记录（模块 11–15，2026-09-03）

### 实施中发现与处置

| # | 类型 | 发现 | 处置 |
|---|---|---|---|
| 68 | 实施发现 | #65 预判的 CJS interop 风险实测消解：cytoscape-dagre 4.x ESM 构建（`dist/cytoscape-dagre.mjs`）**内联打包 dagre 算法**且不 import cytoscape 本体（`register(cytoscape)` 由调用方传入），与 next.config 的 cytoscape CJS alias 天然无冲突 | `import("cytoscape-dagre").then(m => m.default)` + `registerDagre(cytoscape)`，两包并行动态 import |
| 69 | 实施发现 | `@types/cytoscape` 的 `LayoutOptions` 联合类型不含 dagre；dagre 特有字段经 `BaseLayoutOptions`（`name: string`）+ 局部 `as LayoutOptions` 收口 | 集中在 `buildLayoutOptions()` 单函数内，注释说明 cast 理由 |
| 70 | 实施决策 | 布局切换若复用 debounce effect 会有 500ms 延迟；dag 变更路径与布局切换路径解耦 | 独立 `[layout]` effect 立即全量 re-apply；首次渲染 cyRef 为空早退（mount effect 用 latestRef.layout 兜底），fit/debounce 逻辑不动（#65 要求） |
| 71 | 环境发现 | web test:node 出现 13 个与本次改动无关的失败（react-syntax-highlighter ESM 被 CJS require）——干净 HEAD 同样失败，非代码回归；根因：shell 中 `/usr/bin/npm` 解析到 Node 18（不支持 require ESM），项目验证历来用 Node 22 | 以 `PATH=~/.local/bin`（Node 22）运行即 1051/1051 全过；后续验证统一 Node 22 |
| 72 | 实施决策 | mermaid CLI 输出经 rich console 会在窄终端软折行破坏字节保真 | `console.print(..., soft_wrap=True)`（#66）；rich 表格 diff 输出中的用户文本统一 `escape()` 防 markup 注入 |
| 73 | 实施决策 | 模块 15 diff 逻辑独立为纯函数模块（`dsl_diff.py`），CLI 只做文件 IO + 渲染；文档级校验（JSON object + `trace` list）在 CLI 层 `_load_dsl_document` 收口，红字报错 exit 1 | 函数层 6 用例 + CLI 层 5 用例分离测试 |
| 74 | 已知限制 | Python/JS 标签截断对**星面字符**（emoji）计数不同（JS 按 UTF-16 码元、Python 按码点），超限截断位置可能差一位 | parity fixture 保持 BMP 内，代码注释标记该边界；非 ASCII BMP（中文）不受影响 |

### 各模块交付

- **模块 11（DSL 导入预览）**：`web/features/chat/dag/dsl-import.ts`（`dslToDag`：DSL 树直构 SessionDag IR，`dsl:` 前缀节点 id，sibling 边连接分支子链，分支节点按 childCount 进 expandable）、SessionDagPanel Import 按钮（Upload 图标、10MB 护栏、imported 模式 banner + 返回会话）、model.ts 增加 `sibling` 边类型、`web/tests/session-dag-import.test.ts`。实施修复：`messageNodeKey` 参数类型不匹配（重构为 `dsl:` 前缀直出）、导入分支节点未进 expandable。
- **模块 12（deep_research 合并）**：aggregate.ts 导出 `visibleMessagesForDag`（buildVisiblePath **之后**应用 mergeDeepResearchPairs），computeSessionDag 与 dsl.ts convertTrace 共用；Python `_merge_deep_research_pairs` 同步移植；parity fixture 重生成（含配对用例）。实施修复：测试断言未计 ack 用户消息、Python 合并逻辑与 TS 配对规则对齐后 parity 通过。
- **模块 13（dagre 布局）**：`cytoscape-dagre@4.0.1` 依赖；CytoscapeDag 注册扩展 + `DagLayoutName` + `buildLayoutOptions`（dagre: `rankDir TB / nodeSep 40 / edgeSep 12 / rankSep 60`）；面板布局切换按钮（默认 dagre，Network/ListTree 图标，aria-pressed）；i18n 三键（en/zh）。
- **模块 14（Python mermaid）**：`dsl_export.py` 新增 `dsl_to_mermaid`（逐行为移植 dsl-mermaid.ts：sanitize 正则/label 清洗/LABEL_LIMIT=48/branch 虚线边/尾换行）；CLI `session trace --format mermaid`（帮助与错误信息同步 dsl | mermaid）；fixture `session_dsl_expected.mmd`（tsx 生成）+ 4 新测试（字节级 parity/空文档/清洗截断/id 去重），原"mermaid 非法格式"用例参数修正。fixture 再生成需在 web 目录运行 tsx（`@/` alias 解析）。
- **模块 15（DSL diff）**：`kagweb/services/session/dsl_diff.py`（`diff_session_dsl` 纯函数：位置配对、`(kind, tool, round_index)` 递归扁平指纹 Counter 多集比较、modified/added/removed + identical_turns 摘要）；CLI `kagweb session diff <a> <b> [--json]`（rich 表格默认、--json 机器可读、--help 写明位置配对限制与 --stable --normalize-ids 建议）；`tests/services/session/test_dsl_diff.py` 11 用例。E2E 冒烟：改问题文本 + 加 reason 工具的两份导出 → 表格与 JSON 输出均正确。

### 验证

web：test:node **1051/1051** ✔（Node 22）· typecheck ✔ · eslint ✔ · architecture:check ✔（733 模块）· i18n parity ✔；Python：tests/services/session + tests/cli **357 passed, 1 skipped** ✔（含字节级 mermaid parity）· CLI 冒烟：`session trace --help` / `session diff`（rich + --json）✔。

## 代码评审 #75（质量+安全，2026-09-03，模块 11–15 未提交变更）

双验证者交叉复核（静态审读 + 运行时复现），4 个问题全部确认并修复：

| # | 问题 | 严重性 | 修复 |
|---|------|--------|------|
| 75-1 | DSL 导入校验不足：`calls:"xy"`、`trace:[42]` 等类型混淆字段通过 parseSessionDsl（注释声称校验 kinds 实未实现），在渲染期 useMemo 抛 TypeError，web 无 ErrorBoundary → **整页崩溃** | major | parseSessionDsl 增加递归结构校验（node 非空字符串、kind 枚举、calls/branches/branch 形状），异常落入 handleImportDsl 的 catch 转为错误横幅。**唯一性校验被主动放弃**：非 normalizeIds 导出本身合法地在主链/分支链复用相同 call id（计数器按消息重置），拒绝重复 id 会破坏合法导出（实施中发现，2 个既有测试证伪初版方案）；改为 dsl-import nodeKey 带去重后缀（`~N`），导入视图不再静默丢节点 |
| 75-2 | CLI `--format mermaid` 输出经 rich markup 解析：用户文本含 `[/b]` 等闭合标签 → MarkupError 崩溃（端到端复现）；不崩溃时 `[red]` 等字面量被吞也破坏 #66 字节级 parity。dsl JSON 路径与 diff `--json` 同病（前者为既有代码） | major | 三处 `console.print` 增加 `markup=False`；CLI 回归测试（stub KAGWebApp，含 `[/b]` 的 mermaid 输出 exit 0） |
| 75-3 | 导入视图中带调用树的 assistant 节点 tap 被路由到 no-op 的 toggle（dag memo 忽略 expandedMessages）→ 详情面板对这些节点不可达 | medium | dslToDag 不再填充 expandable（调用树已全量物化，无展开语义），tap 落到 select；测试改为断言 expandable 为空 |
| 75-4 | 导入视图元数据必现错误：messageIndex=nodes.length（含 root+前序调用节点）→ 同一会话 live 显示 Q1/Q3、导入显示 Q2/Q6；branchSeq 同源漂移；branchInfo.total 排除选中支少算 1；fork 节点自身 branch 字段未映射 | minor | messageIndex 改为链内序号（分支链从 fork 位置续编）；branchInfo 逐字映射 DSL 的 `entry.branch`（total 含选中支）；实测对照断言锁死 |

验证者修正记录：75-1 的次级向量"重复 node id → cytoscape add 抛错"被证伪（批量 add 静默去重，remove-then-batch 序列使抛错路径不可达），故重复 id 的实际后果是静默丢节点而非崩溃——由 nodeKey 去重一并解决。

修复后验证：web test:node **1054/1054**（新增 5 用例：畸形文档拒绝 ×4、重复 id 接受、去重不丢节点、messageIndex 对照、branchInfo 映射、expandable 为空）· tsc/eslint/architecture ✔ · Python **358 passed**（新增 mermaid markup 回归）✔。

---

# V4：模块 16–20（面板交互 + 后端/SDK + 性能护栏）

范围由用户选定三方向组合：面板交互增强 ×3（16/19/20）、后端/SDK ×1（18）、性能与规模化 ×1（17）。设计基于以下代码事实（2026-09-03 探查）：

- **滚动/闪光锚点已存在**：`ChatMessageList.tsx` L1183-1246 为每 turn 渲染 `data-turn-key={turnAnchorKey(msg, index)}`（`m{id}`/`i{index}`，`web/lib/chat-outline.ts` L57）与 `data-turn-bubble`；`PartnerGroupChat.tsx` L95-116 `jumpToRound` 已实现 scroll+`turn-flash` class 闪烁模式，可直接移植。
- **持久化模式已存在**：`ChatWorkspace.tsx` L373-389 面板开关用 `browserStorage.readRaw/writeRaw("local", "dt:chat:xxx")` + post-mount effect（SSR 首渲染恒 false 防 hydration mismatch）。
- **API 模式已存在**：`kagweb/api/routers/sessions.py` L157-165 `GET /api/sessions/{id}` 走 `store.get_session_with_messages`；`main.py` L526-593 各 router 经 `_auth=[Depends(require_signed_in)]` 注册。
- **护栏缺口已确认**：`dsl-import.ts` materializeCallNodes 递归无深度限制（深嵌套 calls 数组可栈溢出，#75 校验只查形状不查深度）；computeSessionDag/dslToDag 无节点数上限；applyToCanvas 全量 remove+add，1000+ 节点 dagre 布局明显卡顿。

## 模块 16：DAG → 聊天消息定位联动

点击 DAG 中 user/assistant 节点 → 滚动到对应聊天气泡并 flash。**只做单向（DAG→消息）**，不做消息→DAG（聊天列表无对应入口，避免侵入）。
- **交互入口**：详情区（detailRows 下方）加"在对话中定位"按钮，而非节点点击直接跳转（tap 已承载选中/展开，双重语义冲突）；导入视图（无 live messageId）不显示按钮。
- **实现**：SessionDagPanel 新增 `onLocateMessage?: (messageId: number) => void` prop；ChatWorkspace 实现回调：`querySelector('[data-turn-key="m{id}"]')` → 移植 jumpToRound（scrollTo offset-24 smooth + `turn-flash` class + reflow 重启动画）。滚动容器 ref 已存在于 ChatWorkspace（探查确认）。
- **映射**：DagNode.meta.messageId → turnAnchorKey。注意 deep_research 合并后 followup 节点不单独存在（父节点持有合并事件，定位父 id）；负 id（optimistic）消息按钮禁用。
- **评审 #76**：flash class 与滚动逻辑不在 SessionDagPanel 内实现（面板不持有聊天容器 ref，保持渲染组件纯净）；ChatWorkspace 侧封装 `locateMessage(messageId)` 复用既有 CSS（`turn-flash` 动画已全局定义）。风险：消息在虚拟化/折叠分支中时 querySelector 找不到 → no-op 安全降级（不滚动不报错）。

## 模块 17：大规模会话护栏

防两类失控：**深度失控**（栈溢出）与**规模失控**（布局卡顿）。
- **深度限制**：`parseSessionDsl` 校验时传递 depth，calls/branches 嵌套超过 `DSL_MAX_DEPTH = 64` 抛错（DSL 由真实调用树生成，深度即 round→tool→retrieve 层级，64 已远超真实值；10MB 文档的恶意深嵌套在 parse 阶段即拒绝）；`dslToDag` 不再需要自身防护（输入必经 parse）。
- **节点上限**：`dslToDag` 产出节点数超过 `DAG_NODE_LIMIT = 2000` 时截断（保留主链优先）+ 面板顶部警示横幅"已截断，仅显示前 N 个节点"；`computeSessionDag` 超限同样截断调用树物化（保留消息层完整，调用树按 assistant 顺序截断）。
- **性能回归**：新增 fixture 生成 ~1500 节点的 DSL，node:test 断言 dslToDag 在阈值内完成（宽松断言 <2s，防环境抖动）+ 截断路径正确性。
- **评审 #77**：上限值取舍——2000 节点 dagre 在桌面浏览器约 1-2s 一次布局，debounce 后可接受；上限放 5000 则流式期间连续布局会卡死。截断而非拒绝：超大会话仍有部分视图可用。computeSessionDag 截断只裁调用树（round/tool 层）不裁消息层：消息层是会话骨架，调用树是放大器（每消息 10+ 调用节点）。截断需在 expandAll 展开前判断 childCount 总量。风险：truncated 状态需进 SessionDag IR（新增 `truncated: { limit: number } | null` 字段）供面板渲染提示。

## 模块 18：DSL 导出 REST 端点

`GET /api/sessions/{session_id}/trace?format=dsl|mermaid&stable=true&normalize_ids=true&include_text=true`
- **实现**：`kagweb/api/routers/sessions.py` 新增端点，`_auth` 随 router 注册已有；服务层复用 `build_session_dsl`/`dsl_to_mermaid`（模块 14），消息来源 `get_session_with_messages`。响应：dsl → `application/json`（JSONResponse）；mermaid → `text/plain; charset=utf-8`（非 text/html，防 mermaid 文本注入渲染）。404 语义与既有端点一致。
- **facade**：`KAGWebApp` 增加 `export_session_trace(session_id, fmt, **opts)`（SDK 用户直取；CLI 未来可切换到同一路径）。
- **评审 #78**：`include_text` 经 GET query 暴露——默认 true 与面板导出一致（面板导出本身就是用户自己的数据），不视为隐私放大；但端点必须过 `require_signed_in`（同会话详情端点的授权边界，未授权用户不得读取他人 trace）。mermaid 输出用 `PlainTextResponse` 且禁用 `media_type=text/html`。query 参数用 FastAPI `Query` 校验枚举（dsl|mermaid），非法值 422。测试：TestClient + fixture 会话，断言 DSL JSON 结构、mermaid 首行 `flowchart TD`、404、422。

## 模块 19：DAG 面板搜索过滤

面板头部搜索框：输入关键字 → 匹配节点（toolName/query/subagentName/textPreview/capability，大小写不敏感）→ 匹配节点高亮（border 变色）+ fit 只包含匹配节点与路径；无匹配显示计数提示。
- **实现**：纯面板层（不动 aggregate/dsl）——SessionDagPanel 增加 search state，CytoscapeDag 增加 `highlightIds: Set<string>` prop；toElements 对匹配节点加 `highlight` data 字段，stylesheet 新增 `node[highlight = "true"]` 高亮样式；搜索时对 cy.nodes().filter() 屏蔽不匹配节点（`cy.nodes('[highlight != "true"]').hide()` + fit），清空恢复 show+fit。防抖 200ms。
- **评审 #79**：不做 hide() 时"dims 非匹配节点"备选（cytoscape opacity 批量操作同样开销，hide 更明确）；hide/show 走独立 effect 不触发 applyToCanvas 全量重建（保留 pan/zoom 用户状态优先级低于搜索定位——搜索后 fit 匹配子图是新意图，fit 合理）。导入视图同样可用（节点 meta 已含全部字段）。边界：空关键字 = 全显；搜索与 expandable 无耦合。

## 模块 20：面板设置持久化

布局选择（dagre/breadthfirst）与导入视图无关的会话级偏好持久化到 localStorage。
- **实现**：沿用 ChatWorkspace 面板开关模式——`browserStorage.writeRaw("local", "dt:chat:dag-layout", layout)`；SessionDagPanel 内 post-mount effect 读取（SSR 首渲染默认 dagre，无 hydration mismatch）。展开状态（expandedMessages）**不持久化**：会话流式更新后展开集合含过期节点 id，恢复无意义。
- **评审 #80**：只持久化布局单键，避免偏好膨胀；key 命名沿用 `dt:chat:` 前缀（历史品牌缩写保留，与既有 keys 一致而非逐个迁移）。layout state 从 useState 初始化改为 useState + useEffect 读取（默认值与写入值解耦）。

## V4 实施顺序

16（联动，独立）→ 17（护栏，为 19 的 hide/show 提供规模安全）→ 18（后端，与 web 并行）→ 19（搜索，依赖 17 的截断语义）→ 20（持久化，最小）。每模块实施后跑 web+python 定向测试，全量验证收尾。

## V4 交付记录（2026-09-03）

- **模块 16（DAG→消息定位联动）**：SessionDagPanel 新增 `onLocateMessage?: (messageId: number) => void` prop + 详情区"在对话中定位"按钮（LocateFixed 图标；仅 live 节点且 messageId>0 显示，导入快照/optimistic 节点自然排除）；ChatWorkspace 传 `jumpToTurn(\`m${id}\`)` **复用**现有 turn navigator 的滚动+flash+释放流式 pin 逻辑（评审 #76：不重新移植 PartnerGroupChat 的 jumpToRound，零新 CSS）。querySelector 未命中安全 no-op。
- **模块 17（规模护栏）**：model.ts 定义 `DSL_MAX_DEPTH=64`（parseSessionDsl 校验 depth 参数，恶意深嵌套 parse 期拒绝，防递归栈溢出）与 `DAG_NODE_LIMIT=2000`（SessionDag 增 `truncated: {limit, dropped} | null`）；dslToDag NodeBudget 计数器（消息层永不裁，调用树整树跳过）；computeSessionDag 增 `truncate` 选项（**默认 false**——expandAll 导出路径共享此函数且必须全量，#33 契约，评审 #77 关键决策）；面板传 truncate:true + 截断横幅。测试 ×5：深度拒绝、导入截断（500 消息×10 调用=2500 节点）、预算内快速完成（<2s）、live 截断+无截断对照、（合计）。
- **模块 18（DSL 导出端点+SDK）**：`GET /api/sessions/{id}/trace?format=dsl|mermaid&stable&normalize_ids&include_text`（sessions.py；Query pattern 校验非法 422；mermaid 走 PlainTextResponse `text/plain; charset=utf-8` 防 HTML 渲染注入，#78；复用 build_session_dsl/dsl_to_mermaid 与 CLI 同一序列化器）；facade `export_session_trace(session_id, fmt, **opts)`（SDK 直取；ValueError 拒绝非法格式；None=会话不存在）。测试：端点 ×5（stub store，TestClient）+ facade ×4。
- **模块 19（搜索过滤）**：aggregate.ts 纯函数 `searchDagNodes(dag, query)`（匹配 toolName/query/subagentName/textPreview/capability，大小写不敏感；live 与导入视图通用）；CytoscapeDag `highlightIds` prop + `applyHighlight`（removeClass("match")/show → 匹配加 class 高亮 → 非匹配 hide → fit 匹配子图；@types 缺 hide/show 用 StyleableCollection 本地 cast，同 buildLayoutOptions 先例）；applyToCanvas 末尾重放过滤（流式重建不复活隐藏节点）+ 独立 effect（纯可见性变更，不重建不重排）；ResizeObserver fit 改 `:visible`（搜索中 resize 不扩大视野）。面板：防抖 200ms 输入 + 匹配计数。测试 ×1（expandAll 物化后命中 user preview + retrieve query）。
- **模块 20（布局持久化）**：`browserStorage.readRaw/writeRaw("local", "dt:chat:dag-layout")`，post-mount 读取（SSR 首渲染恒 dagre 防 hydration mismatch，同 workspace 面板开关先例）；`changeLayout` 回调写回；展开状态不持久化（流式更新后过期 id 无意义）。react-hooks/set-state-in-effect 新规则豁免（一次性外部 store hydration，注释说明）。

### V4 验证

web：test:node **1059/1059** ✔（Node 22）· typecheck ✔ · eslint ✔ · architecture:check ✔（733 模块）· i18n parity ✔（audit 118 缺失为项目既有，非本次引入）；Python：session+cli+api+app **367 passed, 1 skipped** ✔。i18n 新增 4 键（Locate in conversation / Trace truncated / Search trace / {{n}} matches，en+zh）。

### V4 评审修复（2026-09-03，双子代理交叉验证）

- **#16-locate（Major）**：定位按钮对 assistant 节点静默失效——`data-turn-key` 原先只渲染在 UserMessage 气泡上，assistant 消息走另一渲染分支（仅 `data-chat-message-id`），`scrollToChatTurn` 选择器未命中返回 false 无任何反馈。修复：ChatMessageList assistant 包裹 div 补渲染 `data-turn-key={turnAnchorKey(msg, i)}`（消息 id 跨角色不冲突，两种角色均可定位；flash 回退到包裹 div 的 box-shadow 动画，无新增 CSS）。
- **#79-fit（Minor）**：`applyHighlight` 零匹配时回退 `cy.fit(undefined)` 会按含隐藏节点的全图计算包围盒，与 ResizeObserver 特意使用 `:visible` 的语义自相矛盾。修复：`matched.length > 0` 才 fit，零匹配保持视口（面板已显示 "0 matches"）。
- 安全检查无问题：trace 端点随 sessions.router 挂载继承 `_auth` 依赖（main.py:592）；白名单序列化器 + text/plain + format pattern 校验 + 三重防线（10MB 导入上限 / 深度 ≤64 / 节点 ≤2000）完整。
- 修复后验证：typecheck ✔ · eslint（触及文件）✔ · test:node **1059/1059** ✔。
