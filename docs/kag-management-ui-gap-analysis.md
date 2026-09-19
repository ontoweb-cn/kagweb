# KAG 管理 UI 差距分析（kagweb ↔ openspgapp）

> 对比日期：2026-09-19
> 说明：本文档基于**浏览器实测**（kagweb `:8092`、openspgapp `:8887`）与**源码核验**，给出 KAG 管理面与 OpenSPG 完整控制台的功能差距，供研发后续排期参考。
> 性质标注：每条差距用「设计取舍 / MVP 边界 / 应补齐 / 依赖上游」标注，避免把有意识的设计决策误判为缺陷；排期前请对照 `kag-integration-design.md` 相应章节。
> 合规提示：openspgapp 只做**行为级参考**（不包含、不拷贝、不翻译其代码，同 `kag-integration-design.md` §1/§12）；REST 契约以开源 knext 客户端为权威。

## 1. 背景

- **kagweb**：KAGWeb 应用内置的 `/kag` KAG 管理面，是一个**管理/运维薄层**，通过代理 OpenSPG server 的 `/public/v1/*` REST 接口工作。
- **openspgapp**：OpenSPG server 自带的完整知识图谱管理控制台（知识库、知识模型、数据、应用、权限、设置一体化）。

定位差异决定了差距普遍是"kagweb 只实现管理面最小子集，openspgapp 是完整 studio"。

## 2. 对比环境

| 项目 | 地址 | 登录态 |
| --- | --- | --- |
| kagweb 管理面 | `http://127.0.0.1:8092/kag` | 已登录 |
| openspgapp 控制台 | `http://127.0.0.1:8887` | `openspg` 已登录 |

## 3. 逐页对照（实测）

### 3.1 kagweb 管理面

| 页面 | 实测内容 |
| --- | --- |
| `/kag` 项目列表 | 列出项目（tag + ID）；顶部：刷新 / 新建项目 / 推理任务 |
| `/kag/projects/{id}` 项目详情 | Schema 树（SPG 类型，可增删关系）+ 图标签概览 + 图浏览（只读 DSL）+ 项目成员 + 构建受理 |
| `/kag/tasks` 推理任务 | 只读 kag_solve 摘要，当前为空 |

### 3.2 openspgapp 控制台

| 页面 | 实测内容 |
| --- | --- |
| `#/knowledge` 知识库列表 | 卡片化、可见性标签（私有/外网知识库）、我创建的筛选、搜索、创建知识库；每库带「构建/配置」入口 |
| `#/knowledge/detail/task` 知识库构建 | 左侧子菜单：知识库构建/知识模型/知识探查/召回测试/配置/权限；任务表格（文件类型/导入类型/状态/更新时间）含「详情/日志/删除」「创建任务」；实测到 `KAG_COMMAND…` 执行中任务 |
| `#/knowledge/detail/settings` 知识库配置 | 存在（知识库级配置） |
| `#/knowledge/detail/permission` 权限 | 权限列表 + 权限类型筛选 + 用户搜索（多租户授权） |

## 4. 差距清单

按重要度排序。性质：`设计取舍`（集成设计明确决定，非缺陷）/ `MVP 边界`（首版刻意从简）/ `应补齐`（尚未实现、值得补）/ `依赖上游`（需 KAG 上游化后才会闭环）。

### 4.1 数据接入与构建执行（实测缺口，最高优先）

- kagweb 的 `/projects/{id}/build` 仅为**受理层**（页面标注"仅受理，需远程 executor"），无任务详情/日志/状态跟踪。
  - 参考：`kagweb/api/routers/kag.py` 中 `submit_build`
  - 性质：**依赖上游** — build 已受理，但端到端执行依赖 M5 上游化后配置 executor（`kag-integration-design.md` §9 M4 记录、风险 #5）。
- openspgapp 具备完整**任务生命周期**：创建/详情/日志/删除/状态跟踪（实测见 `KAG_COMMAND…` 执行中任务）。
  - 参考：`openspgapp/.../reasoner/TaskController.java`、`builder/BuilderJobController.java`

### 4.2 数据上传与图探索

- kagweb 无数据上传/导入；图浏览为**只读 DSL** 查询。
  - 参考：`kagweb/api/routers/kag.py` 中 `query_project_graph`
  - 性质：`应补齐` — 数据导入是明确的功能缺口；图浏览经 reason DSL 属**设计既定方案**（M2 已证伪 graph 子图端点），勿作为缺陷返工。
- openspgapp 提供数据导入（`/asyncSubmit`）、搜索、实体详情、一跳图、抽样。

### 4.3 知识模型与概念建模

- kagweb Schema 仅支持关系增删/改名。
  - 参考：`kagweb/api/routers/kag.py` 中 `alter_project_schema`
  - 性质：`MVP 边界` — 首版只读树 + 表单式编辑（`kag-integration-design.md` §5.2）；概念建模/规则/概念树可列为后续增强。
- openspgapp「知识模型」含概念建模、规则管理、概念树等。

### 4.4 应用编排

- kagweb 无 App 概念。
  - 性质：`MVP 边界` — 取决于产品定位；kagweb 是管理/运维薄层，App 编排是否纳入需产品决策，勿仅因 openspgapp 有而列为必补。
- openspgapp 有应用列表 + 创建应用 + 编排/部署/接入/apikey。

### 4.5 全局设置域

- kagweb 单一 KAG 集成设置（server/bridge/绑定）。
  - 参考：`kagweb/web/features/settings/sections/KagSettingsSection.tsx`
  - 性质：`MVP 边界`。
- openspgapp `/setting` 下模型/数据源/图存储/监控/用户配置多维设置。

### 4.6 权限模型

- kagweb 单 admin / 项目成员（member_store），成员写仅 admin。
  - 性质：`MVP 边界` — T2 已实现 membership+归因；完整多租户授权（账号生命周期/资源角色）为 T3 可选演进（`kag-integration-design.md` §6.3）。
- openspgapp「权限」页为完整授权（角色资源、搜索、类型筛选）+ 账号生命周期。

### 4.7 运维与文档侧

- kagweb 无统计/反馈/教程；openspgapp 均有对应能力。
  - 性质：`应补齐`（低优先）。

## 5. 已对齐的点

- 项目创建
- Schema 基本展示
- 图标签 / 图览的只读查询
- 按项目组织

## 6. 建议与结论

kagweb `/kag` 定位为**管理面薄层**（代理 `/public/v1/*`），差距集中在：
1. 数据导入 / 构建执行（任务生命周期）—— **依赖上游**，别急于投入
2. 知识模型 / 概念（MVP 边界）
3. 应用编排（MVP 边界，需产品决策）
4. 多租户权限（MVP 边界，T3 可选）

关于最高优先项的边界：本地 build 仅为受理层，且本实例**无 computing engine driver、受理成功也会执行失败**（`kag-integration-design.md` 风险 #5），端到端执行依赖 **M5 上游化后配置 executor**。因此"补任务详情/日志"只能先增加**可观测性**（让"任务失败/受理"可见），**不能立即交付构建闭环**。若优先做，建议把它拆成两个子项：
- **P0a 任务可观测性**（详情/日志/状态，「受理成功 vs 执行失败」可见）——收益即刻、独立于上游；
- **P0b 构建闭环**（真正执行成功）——**必须等** executor 可配置（M5 上游化）后才有意义。