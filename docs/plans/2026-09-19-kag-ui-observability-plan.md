# KAG UI 完善开发计划（构建任务可观测性 P0a）

状态：**已评审**（2026-09-19 方案评审，修订见 §7；依据
`docs/kag-management-ui-gap-analysis.md` 结论制定）
日期：2026-09-19
合规：openspgapp 仅作**行为级参考**（不包含、不拷贝、不翻译其代码，同
`kag-integration-design.md` §1/§12）；REST 契约以开源 knext 客户端为权威，
实现一律走 OpenSPG `/public/v1/*` 公开接口（与既有 `openspg_client.py` 一致）。

## 1. 背景与定位

- kagweb `/kag` 是**管理/运维薄层**（代理 OpenSPG `/public/v1/*`）。
- gap 分析结论：唯一「收益即刻、独立于上游」的缺口是**构建任务可观测性
  （P0a）**——当前 `submit_build` 仅为受理层，任务状态冻结在提交时的
  `status=RUNNING`（`task_store.py` 的 `answer_digest` 只写一次），
  「受理成功 vs 执行失败」不可见。
- 构建闭环（P0b）、数据导入执行均依赖 M5 上游化后配置 executor，本计划**不投入**。

## 2. 范围与原则

### 2.1 做什么

阶段 A（本计划主交付，独立可交付）：**构建任务可观测性**。

### 2.2 不做什么（明确排除）

| 项 | 理由 |
| --- | --- |
| P0b 构建闭环（真正执行成功） | 依赖 M5 上游化 executor 可配置（gap §6、设计风险 #5） |
| 图浏览改造 | reason DSL 是 M2 既定方案，勿返工 |
| App 编排 | 产品决策项（MVP 边界），勿仅因 openspgapp 有而补 |
| 多租户完整授权 | T3 可选演进 |

## 3. 阶段 A 任务拆解

**目标**：提交构建后，任务列表/详情实时反映 OpenSPG 侧执行状态（受理中 /
执行中 / 成功 / 失败），失败可见节点级日志（traceLog）；OpenSPG 不可达时
优雅降级为本地受理摘要。

**数据源（OpenSPG 公开 REST，经既有代理模式）**：

| 端点 | 用途 |
| --- | --- |
| `GET /public/v1/builder/getById?id=` | `BuilderJob`（`status`/`taskId`=SchedulerJob id/`jobName`/`gmtCreate`） |
| `POST /public/v1/builder/search` | 按 projectId 批量查 BuilderJob（列表合并用） |
| `POST /public/v1/scheduler/instance/search` | 按 `jobId` 查 `SchedulerInstance` |
| `POST /public/v1/scheduler/task/search` | 按 `instanceId` 查 `SchedulerTask[]`（每节点：`title`/`type`/`status`/`traceLog`/`output`） |

### A1 后端

- [x] **A1.1 `openspg_client.py` 新增 4 个 client 方法（信封解包）**
  `kagweb/services/kag/openspg_client.py`：
  - `get_builder_job(job_id)` → `GET /public/v1/builder/getById`
  - `search_builder_jobs(project_id, page_size=100)` → `POST /public/v1/builder/search`
  - `search_scheduler_instances(job_id)` → `POST /public/v1/scheduler/instance/search`
  - `search_scheduler_tasks(instance_id)` → `POST /public/v1/scheduler/task/search`
  - **响应信封（评审 P1-2，已核实）**：builder/scheduler 控制器走
    `HttpBizTemplate.execute2`，响应为 `{result: <payload>}` 信封——与
    project/reason 端点的裸对象响应（现有 `list_projects`/`reason_run`
    直接取值）不同。4 个新方法统一解包 `result`；`search` 的 `result` 是
    分页体 `{results, pageNo, pageSize, total}`，解到 `results` 列表。
  - 沿用 `_request` 模式（JSON 响应、`OpenSPGError` 包装、`_parse_response`
    兜底）；防御 server 偶发空体（归一为 dict/list）。

- [x] **A1.2 `task_store.py` 扩展 build 记录**
  `kagweb/services/kag/task_store.py`：
  - `_normalize` 增补可空字段：`scheduler_job_id`、`status`（旧记录缺失时
    回填空串，列表仍可渲染）。`scheduler_job_id` 保持**可选**（评审 P2-3：
    详情端点惰性回填，不阻塞写入）。
  - 新增幂等更新 `update_task(task_id, **fields)`（复用现有 `_lock` 滚动截断
    逻辑，按 task_id 覆盖）。

- [x] **A1.3 `kag.py` 新路由**
  `kagweb/api/routers/kag.py`（全部走既有 `_require_project_access` +
  membership 过滤 + same-origin 写操作；响应过 `_sanitize` 凭据掩码）：
  - `GET /projects/{project_id}/builds` — 项目构建列表：本地 build 记录 ×
    实时 `BuilderJob.status` 合并（`search_builder_jobs` 批量映射，jobId 对应
    task_id）；OpenSPG 不可达时降级返回本地摘要并标注 `live_status="unknown"`。
  - `GET /builds/{job_id}` — 详情：`get_builder_job` → `taskId` →
    `search_scheduler_instances` → 首个 instance → `search_scheduler_tasks`，
    组装节点级 `{name, type, status, traceLog}` 列表（traceLog 只读、截断 2k）。
    归属校验：经 `project_id` 查 membership（非 admin 仅本项目可见）。
  - 状态归一（A1.4 实测修正）：`BuilderJob.status` **恒 `RUNNING`**（不随执行
    更新），实例级 `status` 可能停在 `WAITING`（DAG 未完）——**成败判定取
    节点级聚合**：任一节点 `ERROR`→failed、全部 `FINISH`→success、有
    `RUNNING`→running、其余（WAITING/WAIT）→pending；无 taskDag 时兜底
    Job.status。节点状态来源：`taskDag.nodes[].properties.status`（列表用）
    或 `SchedulerTask.status`（详情用）。
  - **扩展 `GET /tasks`（评审 P1-1）**：对 build 记录做 live 状态合并——
    `asyncio.gather` **并行**逐条 `get_builder_job`（≤100 条，逐条容错，
    失败降级 `live_status="unknown"`）；inference 行不受影响。任务页
    （`/kag/tasks`）与项目页共用该状态口径。
  - **提交侧不改（评审 P2-3）**：不做提交后回查——详情端点经
    `get_builder_job` 惰性拿到 `taskId`（提交响应体的 BuilderJob 可能不含
    taskId，A1.4 实测确认），`scheduler_job_id` 仅作缓存。

- [x] **A1.4 契约实测（2026-09-19 完成）**
  对已受理任务（job id=1/2，`echo m0-probe`/`echo m4-build-probe`）curl
  `/public/v1/builder/getById`、`/scheduler/instance/search`、
  `/scheduler/task/search`，结论：
  - **`BuilderJob.status` 恒 `RUNNING`**，不随执行更新（昨日提交今日仍
    RUNNING）——仅作「已受理」标识，非状态权威。
  - **失败信号在节点级**：`instance/search` 响应内嵌 `taskDag.nodes[]`
    （`Builder`=computingEngineAsyncTask 已 `ERROR`，`PostProcessor`=
    kagCommandPostSyncTask `WAIT`）；实例级 `status` 停在 `WAITING`
    （DAG 未全终态），**不能**直接映射成败。
  - **traceLog 形态**：`task/search` 每节点一条，`traceLog` 为可读文本
    （时间戳+堆栈）；`cannot find driver for` 出现在 `Builder` 节点
    `ComputingEngineClientDriverManager.getClient`（设计风险 #5 实证）；
    失败任务被调度器**反复重试**（`executeNum=378`），traceLog 持续追加
    增长，展示须截断。
  - 响应均为 `{result: ...}` 信封；`search` 分页字段为 `pageIdx/pageSize/total`。

- [x] **A1.5 后端测试**
  - 新增 `tests/services/kag/test_build_observability.py`：`MockTransport`
    注入式 client 测试（4 个新方法 × 正常/404/空体）+ 路由测试
    （列表合并、状态归一、详情组装、OpenSPG 不可达降级）。
  - 扩展 `tests/services/kag/test_m4b_acl.py`：新端点 membership 门禁
    （非 admin 跨项目 403、admin 放行）。
  - 新端点路由须通过 `tests/api/test_frontend_contract_export.py` 的
    operation_id **唯一性**断言（评审 P3-5）；同步重新生成已提交的
    `openapi.json` 与前端 TS 类型（项目惯例：新端点必须手动更新并重新生成
    前端契约）。

### A2 前端

- [x] **A2.1 `model.ts` 新增类型与解析**
  `web/features/kag/model.ts`：`KagBuildStatus`（`live_status` 归一值 +
  `answer_digest` 兜底）、`KagBuildNode`（`name/type/status/traceLog`）、
  `parseKagBuilds` / `parseKagBuildDetail`（沿用宽松归一 + 安全缺省模式）。
- [x] **A2.2 `api.ts` 新增调用**
  `web/features/kag/api.ts`：`fetchKagBuilds(projectId)`、
  `fetchKagBuildDetail(jobId)`（走 `apiUrl` 相对路径，`scope: "kag"`）。
- [x] **A2.3 任务列表页实时状态**
  `web/features/kag/components/KagTasksPage.tsx`：build 行显示状态 badge
  （执行中/成功/失败/未知，替换冻结的 `status=RUNNING` digest）；展开区对
  build 任务渲染节点状态列表 + traceLog（只读，等宽字体）；刷新以手动为主，
  10s 轮询可选（评审 P3-6，最小改动）。
- [x] **A2.4 构建受理面板回显**
  `web/features/kag/components/MemberBuildPanel.tsx`：提交成功后展示返回的
  jobId + 「在任务列表查看」入口（保留现有成功/错误处理，不新增复杂度）。
- [x] **A2.5 i18n**
  新增状态/日志相关 key（英文原文即键，zh/en 双语），约 8–12 个，沿用
  M4 增量模式。
- [x] **A2.6 前端测试与构建**
  - 扩展 `web/tests/kag-model.test.ts`（`parseKagBuilds`/`parseKagBuildDetail`
    缺字段/漂移降级）。
  - 全量构建校验（项目惯例：含原生依赖时必须跑完整 build，不只 check:fast）。

### A3 验收

1. 后端：`tests/services/kag/` 全绿（沿用 1700+ 后端测试基线，实测 **1757 passed /
   6 skipped**），`check:fast` exit 0。
2. E2E 冒烟（2026-09-19 完成，浏览器实测）：项目详情提交 `echo p0a-smoke` →
   面板显示「构建已受理 + 在任务列表查看」→ `/kag/tasks` build 行显示
   **失败** badge → 展开显示节点（`computingEngineAsyncTask`/
   `kagCommandPostSyncTask`）与 traceLog（可见 "Scheduler execute failed"）。
3. OpenSPG 不可达：任务列表正常渲染，build 状态标注「未知」、**inference 行
   不受影响**、不 500（评审 P2-4）。
4. 权限：非 admin 跨项目访问新端点返回 403。

## 4. 阶段 B — 数据导入（B-1 引导式 KAG_COMMAND 导入，受理层）

状态：**已评审**（2026-09-19 范围决策：B-1；前置核实见下）。

### B-0 前置核实（2026-09-19 完成）

- openspgapp `DatasController` 为 `v1/datas` 内部路径——**无公开文件上传端点**。
- knext 客户端 `BuilderClient.submit()` 是**未实现 stub**（`pass`）——"以 knext
  为权威"的导入提交契约不存在；`write_graph` 是写图接口，非数据接入。
- 数据接入公开契约仅两条路：**KAG_COMMAND**（`/public/v1/builder/kag/submit`，
  阶段 A 已实现提交+可观测）与完整 `BuilderJob` submit（`/public/v1/builder/job/
  submit`，需 pipeline+extension.extractConfig+fileUrl，重契约、无 knext 支撑）。
- **结论**：本阶段做 B-1（引导式 KAG_COMMAND 导入受理层），不做 B-2 完整
  BuilderJob（契约重、执行同样依赖 executor）；文件上传明确排除（无公开端点）。

### B-1 任务拆解（前端为主，后端零改动）

目标：项目详情页新增「数据导入」卡片——常见 KAG builder 命令模板预设 +
自由编辑 + 提交（复用 `POST /projects/{id}/build`）+ 受理回显与任务列表入口
（复用阶段 A 可观测）。UI 明确标注：受理层、执行依赖远程 executor、数据须
放在 executor 可达位置（本面板不承载文件上传）。

- [x] **B1.1 模板预设与命令生成**
  `web/features/kag/`：定义导入模板常量（基于本 fork `kag builder` CLI 真实
  语法，[KAG/kag/bin/commands/builder.py](../../../KAG/kag/bin/commands/builder.py)）：
  - 结构化数据（Git 仓库）：`kag builder --project_id {projectId} --git_url
    <data-repo-url> --commit_id <commit-id>`
  - 非结构化文档（Git 仓库 + 入口脚本）：`kag builder --project_id {projectId}
    --git_url <data-repo-url> --commit_id <commit-id> --entry_script <run-import.py>`
  - 模板标注「示例，请按环境调整」；`{projectId}` 自动填充，`<...>` 占位待填。
- [x] **B1.2 前端「数据导入」卡片 `KagImportPanel.tsx`**
  `web/features/kag/components/KagImportPanel.tsx`：说明文案（受理层/无上传/
  executor 依赖）+ 模板 chips（点击填入命令输入框）+ 可编辑命令输入 +
  提交按钮（复用 `submitKagBuild`）+ 成功回显 taskId + 「在任务列表查看」；
  与构建卡片语义区分（构建=任意命令，导入=引导模板），共享提交实现不重复。
  `KagProjectDetailPage.tsx` 在 `<MemberBuildPanel/>` 后渲染。
- [x] **B1.3 i18n**：新增导入相关 key（zh/en，约 6–8 个），沿用英文原文即键。
- [x] **B1.4 前端测试与构建**：`web/tests/kag-model.test.ts` 无新解析（不新增
  后端解析）；跑 `check:fast` + 完整 `npm run build`（项目惯例）。
- [x] **B1.5 E2E 冒烟**：项目详情选模板 → 提交 → 任务页见 build 行 + 状态 badge。

### B 验收

1. 后端零改动 → 既有 1757 测试基线不受影响（实测 `tests/services/kag/` 全绿）。
2. `check:fast` exit 0；前端 build exit 0（2026-09-19 实测）。
3. E2E（2026-09-19 完成，浏览器实测）：选「结构化数据（Git 仓库）」模板 →
   命令自动填充 `kag builder --project_id 3 --git_url <data-repo-url> --commit_id
   <commit-id>` → 提交受理 → 任务页 build 行「失败」badge + 展开可观测
   （复用 P0a，本实例 executor 缺失预期失败可见）。
4. 权限：提交走既有 build 端点（same-origin + membership），无新攻击面。

## 5. 阶段 C — 后续增强（MVP 边界 / 产品决策项，不默认投入）

| 项 | 性质 | 前置条件 |
| --- | --- | --- |
| Schema 概念建模（规则/概念树） | MVP 边界 | 产品确认 |
| App 编排 | MVP 边界 | 产品决策 |
| 多租户完整授权 | MVP 边界 T3 | 产品排期 |
| 统计/反馈/教程 | 应补齐（低优） | 随手活 |

## 6. 风险与待确认

1. **已实测（A1.4）**：`BuilderJob.status` 恒 `RUNNING`，不随执行更新；
   成败判定取节点级（`taskDag.nodes`/`SchedulerTask.status`）聚合，实例级
   status 仅兜底。A1.4 原待确认项已关闭。
2. `/public/v1/scheduler/*` 与 `/public/v1/builder/*` 沿用 `/public` 无认证、
   信任调用方的既有假设（设计 §3.2/§6.1），内网部署前提不变。
3. 实时状态来自 OpenSPG server 存储，kagweb `task_store` 仍是 advisory 摘要
   （滚动 500 条、不跨进程锁），正确性以 server 为准，UI 标注来源。

## 7. 评审记录

2026-09-19 方案评审（证据：openspgapp 源码 + kagweb 既有实现核实）：

- **P1-1（数据源缺口）**：`/kag/tasks` 列表只读本地 `task_store`，原方案把
  live 合并只放在项目级 builds 端点——任务页拿不到实时状态。修正：扩展
  `GET /tasks` 对 build 记录做并行（`asyncio.gather`、逐条容错）live 合并。
- **P1-2（响应信封，已核实）**：builder/scheduler 控制器走
  `HttpBizTemplate.execute2` → `{result: ...}` 信封；project/reason 走
  `execute` → 裸对象。client 方法须统一解包 `result`（search 解到 `results`）。
- **P2-3（提交路径简化）**：去掉提交后回查 taskId——详情端点惰性经
  `get_builder_job` 解析，`scheduler_job_id` 仅作可选缓存。
- **P2-4（验收补强）**：OpenSPG 不可达时断言 inference 行不受影响、不 500。
- **P3-5（契约导出）**：非全量快照；新端点需过 operation_id 唯一性断言并
  重新生成已提交 `openapi.json`/前端 TS 类型。
- **P3-6（轮询）**：手动刷新为主，10s 轮询可选（最小改动）。
- **风险降级**：失败可见性不依赖 `BuilderJob.status` 更新（SchedulerInstance
  status 必然推进），A1.4 仅决定展示口径。
