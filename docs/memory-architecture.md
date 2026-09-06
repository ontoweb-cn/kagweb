# KAGWeb 记忆体与记忆图谱实现机制

> 代码锚点：后端 `kagweb/services/memory/` · API `kagweb/api/routers/memory.py`（挂载于 `/api/memory`，`api/main.py:579`）· 前端 `web/components/memory/` + `web/lib/memory-graph.ts`
> 本文所有行号以 main 分支（f8a6a26a，2026-09-04）为准。

## 目录

1. [总览：三层记忆模型](#1-总览三层记忆模型)
2. [存储布局与路径约定](#2-存储布局与路径约定)
3. [L1：原始事件层（trace + snapshot）](#3-l1原始事件层trace--snapshot)
4. [L2/L3：Markdown 文档层](#4-l2l3markdown-文档层)
5. [Consolidator：四模式整合引擎](#5-consolidator四模式整合引擎)
6. [读写工具与 chat 集成](#6-读写工具与-chat-集成)
7. [API 端点清单](#7-api-端点清单)
8. [前端工作台](#8-前端工作台)
9. [记忆图谱（MemoryGraph）](#9-记忆图谱memorygraph)
10. [关键设计决策](#10-关键设计决策)

## 1. 总览：三层记忆模型

```
L1 原始事件层（append-only / 刷新式镜像）
  ├─ trace/<surface>/<日期>.jsonl     追加式事件（写入偏好、KB 查询等）
  └─ snapshot/<surface>/              工作区实体镜像（state.json + changes.jsonl）
        │ run_update（LLM 整合，工作台手动触发）
        ▼
L2 每 surface 摘要层
  └─ L2/<surface>.md × 7（chat/notebook/quiz/kb/book/partner/cowriter）
        条目脚注 ref = "<surface>:<entity_id>"，指回 L1 实体
        │ run_update（LLM 整合）
        ▼
L3 跨 surface 综合层
  └─ L3/{recent, profile, scope}.md   ref = 裸 surface 名（指向 L2 文件）
     L3/preferences.md                仅由 write_memory 工具写入，永不自动整合
```

形成 **L3 → L2 → L1** 的三段引用链：L3 脚注指向 L2 文件，L2 条目脚注指向 L1 实体，L1 实体可深链回源（chat 会话、notebook 记录等）。

访问入口为无状态门面 `get_memory_store()`（`services/memory/store.py:434`，进程级单例）；整合由 `consolidator/` 中的 run 管理器驱动。

## 2. 存储布局与路径约定

路径解析集中在 `services/memory/paths.py`（docstring 即权威布局定义）：

| 路径 | 层 | 说明 |
|------|----|------|
| `trace/<surface>/<YYYY-MM-DD>.jsonl` | L1 | 按 UTC 日分文件的追加式事件流 |
| `snapshot/<surface>/state.json` + `changes.jsonl` | L1 | 实体指纹镜像 + git-log 式变更日志 |
| `L2/<surface>.md` + `<surface>.meta.json` | L2 | 7 个 surface 各一份 + sidecar |
| `L3/<slot>.md` + `<slot>.meta.json` | L3 | 4 个槽位（recent/profile/scope/preferences）+ sidecar |
| `backup/<timestamp>/` | — | v1 迁移归档（PROFILE.md/SOUL.md/SUMMARY.md） |

- **surface 白名单（7 个）**：`chat | notebook | quiz | kb | book | partner | cowriter`（`paths.py:48-56`）
- **L3 槽位（4 个）**：`recent | profile | scope | preferences`（`paths.py:57`）
- 记忆根目录经 `PathService` 惰性解析（支持多用户）；`_memory_path_service` ContextVar（`paths.py:33-45`）允许 partner 运行时把读写重定向到 owner 的记忆树——IM 伙伴的 `read_memory/write_memory` 操作的是 owner 的记忆，而 partner 自己的偏好走独立工具落到 partner 记忆树。
- 可调参数集中在 `data/user/settings/main.yaml` 的 `memory:` 子树（`settings.py`），含各模式 budget（L2/L3 条目预算，默认 20/10）、dedup 迭代上限（默认 3）、切块 overlap（默认 0.10）等，保存时做类型强制 + 数值钳制。

## 3. L1：原始事件层（trace + snapshot）

L1 有两套互补机制——**trace 回答"做过什么"，snapshot 回答"存在什么"**（`recall.py` docstring）。

### 3.1 trace（追加式事件）

- 事件结构 `TraceEvent`（`trace.py:35-63`）：`id`（`<surface>:<ULID>`）、`ts`、`surface`、`kind`、`payload`、可选 `session_id/turn_id`。
- 全仓库仅 3 处发射点：
  1. `write_memory` 工具 → `chat` surface 的 `preference_stated`（`tools/builtin/__init__.py:869`）
  2. RAG 检索完成 → `kb` surface 的 `query`（`services/rag/service.py:165-182`，best-effort）
  3. `partner_memorize` → `partner` surface 的 `preference_stated`（`tools/partner_memory.py`）
- **chat 逐轮对话不走 trace**——chat surface 的 L1 实体来自 snapshot 适配器直接读 chat 历史 SQLite。
- 写入按 surface 加 `asyncio.Lock`，实际写盘 `asyncio.to_thread` 追加；**失败只告警绝不抛出**——记忆捕获不能破坏产生它的业务流程。

### 3.2 snapshot（工作区实体镜像）

- `snapshot/entity.py`：`Entity {id, label, ts, content, fingerprint(sha1 前 16 位)}`；`EntityStamp` 是无 content 的廉价探针。
- `snapshot/adapters.py`：每 surface 一个只读适配器（notebook/cowriter/book/partner/kb/chat/quiz），从各自数据源（JSON manifest、SQLite 只读 URI、partner 会话 JSONL）抽取实体并计算指纹。chat 适配器内联全部对话块，指纹取 `sha1(last_msg_id, updated_at)`；探针路径用窗口函数逐表达式镜像全量指纹公式（注释明确禁止 `MAX(id)`，会与时钟偏差分歧）。
- `snapshot/diff.py`：纯函数集合差 → `added | modified | removed`。
- `snapshot/store.py`：`refresh_snapshot()` = stamps 读 → diff → 追加 `changes.jsonl` → 保存 `state.json`（幂等，无变化不写）；`pending_changes()` 只读预览。探针失败回退全量读取，避免把故障误判为"全部删除"。

## 4. L2/L3：Markdown 文档层

文档模型在 `services/memory/document.py`（纯函数，无 I/O 无 LLM）：

```markdown
# <标题>

## <分节>
- <事实文本，≤240 字符> [^1][^2] <!--m_xxx-->

---

[^1]: chat:01HZ...      ← L2：指向 L1 实体
[^2]: notebook:...
```

- 条目 id：`m_<ULID>`（26 字符 Crockford-base32，前 10 位毫秒时间戳，天然时间有序）。
- 解析兼容两代格式：新式 `<!--m_xxx-->` HTML 注释锚点 + 整数脚注；旧式 `[^m_xxx]` 直接标注（下次保存自动迁移）。
- `serialize()` 合并去重脚注：引用同一来源的 N 条条目共享一个整数标签，按首次出现顺序编号；round-trip 幂等。
- **L3 的 ref 是裸 surface 名**（`ids.py:36` 白名单）——指向 L2 文件而非条目；前端图谱据此区分 strong/soft 边（见 §9）。

批量编辑用原子 ops（`ops.py`）：`AddOp/EditOp/DeleteOp`，全批校验通过才应用（text ≤240 字符、refs 必须合法、同批对同一 id 既 edit 又 delete → 整批拒绝）；delete 的 `reason` 限定 `{contradicted, superseded, stale, low-signal}`。

## 5. Consolidator：四模式整合引擎

`services/memory/consolidator/`，LLM 驱动，**全部由工作台/API 手动触发，无后台自动任务**。运行管理器 `runs.py`：每 `(layer, key)` 最多一个活动 run；事件经有界 ring 缓冲支持 SSE 重放（`since=<cursor>`）与断线重连；每次原子写前保存 undo 检查点，可 `POST /runs/{id}/undo`；run 与客户端解耦（断开后继续跑）。

| 模式 | LLM 参与方式 | 输入 → 输出 | 要点 |
|------|-------------|------------|------|
| **update** | 每 chunk 一次调用，产出事实 | L1 新增事件 → L2 新条目；L2 新增条目 → L3 | meta.json 的 **id 集合差**求增量（`seen_entity_refs` / `seen_l2_entry_ids`），对 mtime/时区/重放稳健；每 chunk 落盘 |
| **audit** | 行级编辑（replace/delete/insert） | 带注释行号视图（每条 bullet 附原始证据全文）→ 修正后的文档 | "IDE 式"行号视图；按行号**降序**应用避免漂移；单条失败只拒绝该条 |
| **dedup** | 行级编辑（**仅 replace/delete，禁 insert**） | 全文档 → 去重后文档 | 迭代式，0 条编辑即提前收敛；`iterations` 是上限不是配额 |
| **merge** | **无 LLM** | 纯机械脚注整理 | 迁移旧脚注布局、折叠重复脚注、重编号；幂等 |

公共设施：

- **切块**（`chunker.py`）：按 budget 目标大小切块，右边界扩展到段落/句子边界绝不截断句中，相邻 chunk 按 overlap_ratio 重叠。
- **引用完整性由代码保证**（`references.py`）：update 的可引用池 = 与 chunk span 相交的实体标记（chunk 局部池，随切块附给 LLM）；`validate_fact_refs` 丢弃 LLM 幻觉引用；L3 的可引用池是 chunk 内出现的 surface 头。
- **L3 客观性护栏**（`guards.py`）：禁用绝对化措辞词表（"深刻/总是/deeply/never"等中英文）。
- **链式触发**：update →（有新增且 `auto_after_update`）dedup →（`auto_after_merge`）merge；audit/dedup 结束同理可链 merge。
- **preferences 三重拦截**：`store.update_l3` raise、`run_update` raise、API `/runs/start` 405——唯一写入方是 `write_memory` 工具。

## 6. 读写工具与 chat 集成

### 6.1 工具（`tools/builtin/__init__.py`）

- **read_memory**（L770-797）：无参数，返回 `read_l3_concat()`——按 `recent → profile → scope → preferences` 拼接四个 L3 文档。**条件自动挂载**：`user_has_memory()` 探测 L3 是否非空（fail-closed，异常一律不挂）。
- **write_memory**（L800-902）：唯一 chat 写记忆工具，参数 `op(add|edit)` + `text` + 可选 `target_id/reason`。执行顺序：**先发 L1 trace 事件**（`preference_stated`，使偏好条目脚注指向真实事件）→ `store.write_preference()`（按文件锁 + **幂等去重**：空白/大小写不敏感匹配已有条目，命中则返回 "already saved... skipped duplicate"，给模型显式信号阻止重复调用，对抗长回合重复写入）。**常开自动挂载**。

### 6.2 注入路径（`services/session/turns/executor.py`）

1. 前端随 turn payload 发 `memory_references`（白名单 `recent|profile|scope|preferences|summary`，来自 `MemoryPicker` 选择器）。
2. executor **按轮惰性读取**：非空才 `read_l3_concat()`（`executor.py:371-372`），未选择零开销。
3. 装入 `UnifiedContext.memory_context`（`core/context.py:115`）→ `prompt_blocks.py:99-100` 生成 `PromptBlock("memory")`，位于 persona 块之后、tools 块之前。
4. 划词辅导（selection tutor）回合 `allowed_builtin_tools=[]`，与全局记忆完全隔离。
5. partner 运行时：chat 的 read/write_memory 被 suppress，强制改用 `partner_read/partner_memorize/partner_search`（`tool_composition.py:209-215`）；`builtin_whitelist` 减法闸门允许对所有者禁用 read_memory。

前端仅在工具轨迹中展示动词（`TracePresentation.tsx:270-280`）：`read_memory` → "Recalling memory"、`write_memory` → "Saving memory"。

## 7. API 端点清单

路由 `kagweb/api/routers/memory.py`（27 个端点），按功能分组：

| 组 | 端点 | 说明 |
|----|------|------|
| 总览 | `GET /overview` | 11 份文档状态 + L1 backlog（自上次 L2 整合以来的新事件数）+ backup 列表 |
| 文档 | `GET/PUT /doc/{layer}/{key}`、`GET /doc/{layer}/{key}/lines`（行号视图）、`DELETE .../entry/{id}`、`POST .../reset` | reset 删 md+meta，下次 update 全量重摄取；run 活跃时 409 |
| 运行 | `POST /runs/start`、`GET /runs/{id}`、`GET /runs/{id}/events?since=N`（**SSE**）、`POST /runs/{id}/cancel`、`POST /runs/{id}/undo`、`GET /runs?layer=&key=` | update/audit/dedup/merge；preferences 仅允许 dedup/merge |
| L1 | `GET /trace/{surface}`、`DELETE /trace/...`、`GET /snapshot/{surface}`、`POST /snapshot/{surface}/refresh`、`GET/DELETE /snapshot/{surface}/changes` | snapshot refresh 把 pending diff 提交进 changes.jsonl |
| 引用 | `GET /resolve_entry/{entry_id}` | L3 脚注（`m_xxx`）→ 定位属主 L2 surface |
| 设置 | `GET/PUT /settings` | `memory:` 设置子树 |

另有旧版内联 SSE 端点 `POST /doc/{layer}/{key}/update|audit|dedup`，现为 `/runs/start` 薄包装。

## 8. 前端工作台

路由（`web/app/(utility)/memory/`）：Hub 首页、`graph`、`l1`、`l2[/surface]`、`l3[/slot]`、`resolve`（m_ 引用重定向 shim）。组件集中在 `web/components/memory/`。

### 8.1 三层工作台

- **L1 工作台**（`MemoryL1Workbench` + `MemorySection.tsx` 的 `L1View`）：7 个 surface 并行拉 `/api/memory/snapshot/{surface}`，显示实体列表 / pending 变更（琥珀色）/ KB 查询历史三个 Tab；Refresh 提交 diff；**只读**，无 LLM 面板；window focus / visibilitychange 自动重拉。每个实体的深链映射回源页面（chat→`/chat/{id}`、book→`/books/...`、quiz→`/chat/{sessionId}` 等）。
- **L2/L3 工作台**（`MemoryWorkbench`，两层共用）：左侧文档导航（entry_count 徽标）+ 中间渲染（渲染/行号双视图）+ 右侧 360px `MemoryRunPanel`。markdown 预处理把 `<!--m_xxx-->` 注释转为零宽锚点 `<span id="m_xxx">`，脚注定义自动 linkify——L2 脚注 `surface:id` → `/memory/l1?ref=...`；L3 脚注 `m_xxx` → `/memory/resolve?id=...` → `/memory/l2/{surface}?focus=m_xxx`（滚动 + 1.8s 闪烁定位）；L3 裸 surface 名 → `/memory/l2/{surface}`。
- **运行面板**（`MemoryRunPanel` + `useMemoryRun`）：选择模式（update/audit/dedup）、budget/iterations、模型（`llm_selection`）；**事件驱动而非轮询**——SSE 流 + seq 游标断线 1s 重连 + localStorage 持久化 run_id 跨页面刷新恢复；事件流按 `llm_io_start/delta/end` 重组为对话卡（可看 system/user prompt 与模型响应），14 种系统事件各有图标与色调；支持 undo（深度可见）与文档 reset。

## 9. 记忆图谱（MemoryGraph）

**无后端专用图谱 API、无节点/边存储、无图库依赖**——`MemoryGraph.tsx` 是手写 SVG 渲染，数据由 `web/lib/memory-graph.ts` 在客户端从 17 个既有 memory API 并行组装（7×snapshot + 7×L2 doc + 3×L3 doc，单请求失败静默降级为空）。

### 数据结构

- `GraphNode {id, layer, cluster, section, label, preview, href, x/y/r}`：id 全层唯一（`L3:{slot}:{entry_id}` / `L2:{surf}:{entry_id}` / `L1:{surf}:{entity_id}`）。
- `GraphEdge {source, target, kind: "strong"|"soft"}`：**strong** = 引用指向具体条目（L2 脚注 → L1 实体；L3 脚注 `m_xxx` → L2 条目）；**soft** = 仅 surface 级引用（L3 裸 surface 名 → 该 surface 的合成锚点节点 `L2:{surf}:__anchor__`，r=0 隐藏，位于簇质心）。
- 构建前按 id 去重（quiz 的 `session:question` 复合 id 会跨源重复）。

### 布局（确定性，非力导向）

- **三环同心圆**：L3 内环（r=6）/ L2 中环（r=4.5）/ L1 外环（r=2.8）。
- 同一 surface 的 L1+L2 **共享同一角度扇区**，角度按条数弹性加权（单簇保底 2.5%，L3 slot 保底 12%；profile 从 12 点钟起始）。
- 节点撒点用**确定性抖动六角栅格**（整数哈希 2654435761/1597334677），同一数据每次渲染坐标稳定。

### 渲染与交互

- **着色按层**：L3 = 主色原值，L2/L1 逐层向前景色混合降饱和（color-mix，深色模式自适应）；边颜色取两端点中更"合成"的一层。
- 边为二次贝塞尔曲线，控制点向画布中心收拢（hub-and-spoke 束感）；透明度分级（常态/高亮/压暗）。
- 交互：点击锁定节点并触发 **2 跳 BFS 高亮**（可点亮 L3→L2→L1 引用链，非邻接元素压暗至 0.04）；悬停 HoverCard 锚定节点屏幕坐标（显示层/簇/分节 + 6 行预览）；滚轮以光标为不动点缩放（0.35–4×）；拖拽平移；L1/L2/L3 三层显隐开关（边按"两端层均开启"过滤）。

## 10. 关键设计决策

1. **一切写路径有锁**：L1 按 surface 锁（trace.py）、L2/L3 按文件路径锁（store.py `_write_locks`）、run 按 `(layer, key)` 互斥（runs.py）。
2. **一切落盘原子**：tmp + `os.replace`（store / consolidator runtime / meta sidecar 三处，meta 版带 fsync）。
3. **增量 = id 集合差**（meta.json sidecar 的 seen-* 集合），对 mtime/时区/时钟偏移/事件重放稳健；run 崩溃重启无状态残留。
4. **引用完整性由代码而非 LLM 保证**：chunk 局部 ref 池 + 幻觉引用过滤 + ref 形状按层校验（L2 文档中出现 `m_<ULID>` 形 ref 即视为幻觉）+ ops 批量原子校验。
5. **preferences.md 是特殊路径**：三重拦截禁止自动整合；唯一写入方是 `write_memory` 工具；幂等去重对抗模型重复写入（issue #647）。
6. **记忆捕获永不破坏业务**：L1 写失败只告警；L1 读失败回退全量探针；整合失败保留第一阶段结果。
7. **图谱零后端成本**：复用既有 3 组只读 API 在客户端组装，确定性布局保证渲染稳定，不引入图数据库或图库依赖。
