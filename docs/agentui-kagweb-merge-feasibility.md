# AgentUI 与 KAGWeb 功能合并可行性分析

- 分析日期：2026-09-18
- 合并方向：以 KAGWeb 为宿主，选择性引入 AgentUI 的功能
- 分析对象：本地 `kagweb` 与同级 `agentui` 仓库
- 状态：代码级可行性评估，尚未实施合并

> 本文基于本次代码检查，不代表已通过构建或端到端兼容性验证。AgentUI 本地缺少安装依赖，本次未执行其测试、类型检查或构建，也未启动两个项目进行联调。下文代码行号对应分析时的版本。AgentUI 链接按两个仓库位于同一级目录组织；单独发布本文时需要替换为相应仓库链接。

## 一、结论

**合并可行，但推荐“以 KAGWeb 为运行与数据底座，选择性迁入 AgentUI 的功能模块”，而不是整体合并两个应用。**

两边的优势互补：KAGWeb 更适合承担统一会话、执行、持久化、鉴权和多入口；AgentUI 更有价值的是知识库管理、工作流画布及面向 Agent 的工作界面。

但必须明确：**AgentUI 的许多功能依赖外部 Intellect 服务，迁入界面不等于获得对应的后端能力。**

总体判断：

- 选择性功能合并：可行性高，推荐。
- 知识库界面配合外部 Intellect RAG：有明确集成路径。
- 完整画布迁移：可行，但应单独立项，不能按普通页面迁移估算。
- 整体搬入 AgentUI 前端与 BFF：不推荐，会重复会话、认证、配置和协议体系。
- 不依赖外部服务、仅靠合并两个仓库获得完整 RAG/工作流产品：当前代码不支持这个判断。

## 二、两个项目的实际定位

| 维度 | KAGWeb | AgentUI |
| --- | --- | --- |
| 前端 | Next.js 16、React 19 | Vite、React 18、React Router 7 |
| 服务端 | Python、FastAPI | Node.js、Hono BFF |
| 执行入口 | CLI、WebSocket、Python SDK 共用执行链路 | 主要通过 Web 调用 BFF 和外部服务 |
| Agent 接入 | CLI、ACP、HTTP Agent Loop | 多种 Harness Adapter |
| 会话与事件 | SQLite/PocketBase，持久化事件与断线回放机制 | 部分远端持久化，通用适配器使用内存会话 |
| 知识库/RAG | 当前分支已移除对应子系统 | 有管理界面，主要依赖 Intellect RAG |
| 工作流画布 | 没有对应完整编辑器 | 有实质实现，但与宿主及 Intellect DSL 耦合 |
| 产品形态 | 可扩展的 Agent 应用框架 | 面向多个外部服务的综合操作界面 |

### 两个关键前提

1. **KAGWeb 并不是“已有完整 KAG/RAG，只缺漂亮界面”。** 当前代码中的知识库和 RAG 子系统已经移除；保留的文档解析、附件处理不能替代知识库索引和检索。
2. **AgentUI 也不是自带完整 Agent、RAG、工作流引擎的一体化系统。** 很多能力由外部服务提供，BFF 负责适配或转发。

依赖入口：

- [KAGWeb 前端依赖](../web/package.json)
- [AgentUI 前端依赖](../../agentui/package.json)
- [AgentUI BFF 依赖](../../agentui/bff/package.json)

## 三、值得合并的功能

### 3.1 知识库、文件与检索界面

**价值高，适合优先集成。**

AgentUI 已有文件上传、目录操作、知识库关联、文档解析配置、索引运行和检索相关服务代码。

代码依据：

- [文件管理页面](../../agentui/src/pages/files/index.tsx)，第 30 行起。
- [知识库服务接口](../../agentui/src/services/knowledge-service.ts)，第 222 行起。

但需要区分两种文件：

- **KAGWeb 会话附件**：服务于对话，已有存储、提取和上下文注入链路。
- **AgentUI 知识库文档**：需要长期管理、索引、检索以及外部数据集标识。

建议保留这两个概念，通过“将附件加入知识库”等显式操作连接，不能直接合并成同一种存储记录。

第一阶段继续使用外部 Intellect RAG，由 KAGWeb 新增服务适配层和受鉴权保护的管理 API，比在 KAGWeb 内重建整个 RAG 引擎更现实。

### 3.2 工作流画布

**差异化价值最大，但不是即插即用。**

AgentUI 的画布有真实实现，包括：

- 节点与连线编辑；
- 保存、导出和版本；
- 全局变量与节点配置；
- 调试、执行日志和 Webhook 界面。

代码依据：[画布编辑器](../../agentui/packages/canvas-plugin/src/editor/index.tsx)，第 24 行起。

名为 `canvas-plugin` 不代表它已经独立：

- 包内有 334 个 TypeScript/TSX 文件。
- 静态统计发现 132 个不同的宿主 `@/…` 导入目标。
- 依赖宿主的 UI、路由、鉴权、模型列表、知识库接口和翻译。
- 图结构与 Intellect 可执行 DSL 相互绑定。

代码依据：

- [包入口](../../agentui/packages/canvas-plugin/src/index.ts)，第 7 行起。
- [DSL 转换](../../agentui/packages/canvas-plugin/src/editor/utils/dsl-bridge.ts)，第 85 行起。

建议将其作为独立集成项目：先迁移编辑器并保留外部执行，再考虑执行协议统一。不应把 React Flow 图、KAGWeb 的执行轨迹和后端可执行工作流视为同一种数据。

抽取时需要定义由宿主提供的接口，覆盖持久化、执行/调试、模型和数据集目录、上传、导航及身份。不能仅替换请求 URL。

### 3.3 聊天与 Agent 工作界面

**选择性吸收，不整体替换。**

AgentUI 有审批卡片、澄清卡片、工具事件展示，以及任务进度、工具面板等组件。

不过 KAGWeb 已有思考展示、活动面板和用户交互组件，后端协议也已覆盖：

```text
approval_request / clarify_request
content / thinking
tool_call / tool_result
progress / usage / error
```

代码依据：[KAGWeb AgentLoop 协议](../kagweb/services/agent_loop/protocol.py)，第 34 行起。

这部分应当是交互和布局增强，不是把 KAGWeb 聊天系统换成 AgentUI。

成熟度限制：AgentUI 三栏工作界面的根布局目前传入 `tasks={[]}`、`panels={[]}`。它提供了界面框架，尚不能当作已经打通的任务管理系统。

代码依据：[AgentUI 根布局](../../agentui/src/layouts/root-layout.tsx)，第 40 行起。

### 3.4 Memory、Skills、Search 等管理功能

这些模块有实际服务代码，但数据和执行语义仍主要在外部服务中。

可以作为独立业务模块迁入，不建议一次性把所有导航和页面都搬过来。每个模块应先明确接入哪个服务、谁拥有数据、怎样授权，再迁移界面。

认知推演模块也有实际实现，但属于独立业务域，不应成为基础合并的前置依赖。

## 四、应保留的 KAGWeb 核心

### 4.1 统一会话与执行链路

建议继续让以下链路成为唯一主路径：

```text
CLI / Web / Python SDK
          │
          ▼
KAGWeb 会话与回合服务
          │
          ▼
ChatOrchestrator / Capability
          │
          ▼
AgentLoopBackend 或业务服务适配层
          │
          ▼
外部 Intellect / KAG / 其他服务
```

KAGWeb 已有事件持久化、顺序控制和回放代码。最终完成事件会等待前序事件落盘，不宜替换成 AgentUI 的浏览器临时快照或进程内会话。

代码依据：

- [会话数据库结构](../kagweb/services/session/sqlite_store.py)，第 165 行起。
- [事件落盘与完成处理](../kagweb/services/session/turns/lifecycle.py)，第 510 行起。

业务管理类 CRUD 可以通过受保护的 API 路由实现，不必全部塞入对话回合；需要跨 CLI、Web 和 SDK 统一执行的工作才适合接入 Capability。

### 4.2 统一身份与权限

不要同时保留两套独立的：

- 登录状态；
- 用户与租户解析；
- 后端凭据；
- 管理员权限；
- 会话归属规则。

AgentUI 的部分通用认证中间件主要检查凭据是否存在；通用适配器的内存会话也没有完整利用用户、租户上下文。这些机制不适合直接变成合并后的权限边界。

代码依据：

- [AgentUI 认证中间件](../../agentui/bff/src/middleware/auth.ts)，第 30 行起。
- [通用 OpenAI 适配器](../../agentui/bff/src/services/adapters/shared/openai-base-adapter.ts)，第 73 行起。

应由 KAGWeb 在服务端确定用户身份，再将允许的身份信息传递给上游。外部服务的原有授权仍需保留，不能只靠前端隐藏菜单。

KAGWeb 现有 CLI 子进程环境变量白名单、工作目录限制、用户凭据归属及服务访问授权也应保留，不能为了兼容迁入模块而绕过。

### 4.3 统一 Agent 接入

两边已有 Intellect、Hermes、AgentScope 等接入，不能简单叠加。

尤其是 AgentUI 通用 OpenAI 适配器当前只构造一条用户消息，历史接口为空，取消操作没有实际实现。它不能直接替代 KAGWeb 的 Agent Loop。

建议保留 KAGWeb 的接口，吸收 AgentUI 独有的上游协议处理，而不引入第二套 Backend Registry。

AgentUI 的 KAG 适配器通过远程 MCP 调工具；KAGWeb 当前明确移除了通用 MCP 工具层。如需保留 KAG 功能，应封装到外部服务或专用能力边界，不要顺手恢复整套 prompt-time 工具系统。

## 五、主要合并难点

### 5.1 前端不是直接复制即可

React 18 → 19、Vite → Next.js 会涉及：

- React Router 改为 Next 路由；
- `import.meta.glob` 模块注册需要替换；
- 浏览器专用编辑器需要客户端边界；
- LESS、Tailwind、组件主题和翻译需要适配；
- React Flow、Lexical、Monaco 等依赖需要逐项验证兼容性；
- 包体积较大的功能需要按路由懒加载。

AgentUI 的 API 常量有根路径假设，而 KAGWeb 明确要求子路径部署。迁移后的请求必须使用 `apiUrl()` / `wsUrl()`，不能原样复制网络层。

代码依据：

- [AgentUI 功能注册](../../agentui/src/features/_registry.ts)，第 13 行起。
- [AgentUI API 常量](../../agentui/src/utils/api.ts)，第 3 行起。

### 5.2 两套消息协议不能并存为主协议

AgentUI 同时兼容 Gateway 会话和旧 RAG 会话，还会将标准化事件重新包装成旧版 SSE 消息。

代码依据：

- [聊天协议选择](../../agentui/src/pages/next-chats/hooks/use-send-chat-message.ts)，第 90 行起。
- [BFF 事件转换](../../agentui/bff/src/routes/bff-agents.ts)，第 656 行起。

建议：

1. KAGWeb `StreamEvent` 保持主协议。
2. AgentUI 组件使用转换后的展示数据。
3. 上游特有事件在后端适配。
4. 不把旧 RAG SSE 格式扩散到 KAGWeb 核心。

KAGWeb 请求模型有严格字段校验，WebSocket 协议也有版本约束。新增回合字段或交互命令时，需要同步服务端模型、前端生成契约和相应测试，不能只在 UI 中新增字段。

### 5.3 数据需要映射，而不是覆盖

未来迁移会话和知识库时，需要明确维护：

```text
KAGWeb 用户 / 会话 / 回合
              ↔
外部服务 / 租户 / 会话 / Run / Dataset
```

外部 session ID 不应直接充当 KAGWeb 的 session ID；审批请求也需要绑定具体用户、回合和上游 Run。

外部服务保持其自身数据的权威来源；KAGWeb 保存所属关系和必要的映射、索引或展示缓存。避免未经设计就在两个系统中双写同一份业务数据。

### 5.4 代码授权需先确认

AgentUI 仓库没有找到项目级 LICENSE，相关包也未声明许可证。`private: true` 不是代码复用许可。

如果两个项目都由同一团队拥有，可以通过确认权属、第三方来源并补齐声明解决；在复制代码和对外发布前，这仍是必要前置条件。

## 六、推荐的分阶段合并方式

| 阶段 | 范围 | 验收重点 |
| --- | --- | --- |
| 1. 确立边界 | 权属、身份、数据归属、事件映射和依赖清单 | 不出现第二套主会话或权限体系 |
| 2. 小规模迁移 | 一个工作面板或少量展示组件 | 兼容 Next/React 19，不破坏现有聊天 |
| 3. 知识库闭环 | 列表、上传、索引状态、检索，连接外部 RAG | 用户隔离、错误反馈、附件与文档区分 |
| 4. 画布集成 | 编辑、保存、执行、日志 | DSL 往返一致，执行和取消语义明确 |
| 5. 按需扩展 | Memory、Skills、Search 等 | 服务可选，未配置时不暴露失效入口 |
| 6. 数据与部署收敛 | 历史数据迁移、旧入口退役 | 可回滚、子路径部署、回放与权限回归 |

最有代表性的首个业务闭环是：

**知识库文档上传 → 索引状态 → 检索结果展示。**

它能验证前端迁移、外部服务适配、身份和数据映射，而不必先改动 KAGWeb 最敏感的对话执行核心。

长期不推荐保留“两个完整前端 + 两套主后端”的结构。嵌入原 AgentUI 可以临时演示，但只是并置运行，不是功能真正合并。

## 七、验证范围与限制

本次已完成：

- 检查两个项目的架构、依赖声明和关键实现。
- 对比 Agent 接入、会话、事件、身份和存储边界。
- 区分已实现功能、未接通的界面框架和依赖外部服务的功能。
- 检查画布对宿主的静态导入耦合。
- 核实 KAGWeb 前端技术栈及已有聊天相关组件，避免将重叠能力误判为缺失。

本次未完成、也不应从本文推断为已完成：

- AgentUI 依赖安装、测试、类型检查或构建。
- 两项目运行时联调或浏览器交互验证。
- 外部 Intellect、KAG、RAG 服务的真实协议兼容性测试。
- 数据迁移验证、完整安全审计或性能评估。
- 任何业务代码合并。

因此，本文结论是静态架构与实现评估，不是已通过运行验证的兼容性结论，也不构成对两个项目生产就绪程度的认证。
