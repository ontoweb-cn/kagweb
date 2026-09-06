# DeepMentor 前端架构

> 适用版本：Next.js 16.2.3 · React 19 · TypeScript 5 · Tailwind CSS 3.4（`web/package.json` 为准）
> 代码根目录：`web/`；本文所有路径均相对 `web/`。

## 评审修正记录（2026-09-04，逐条对照代码核实）

| # | 严重度 | 问题 | 修正 |
|---|---|---|---|
| 1 | 中 | 分层规则把 shared/ 与 lib/ 合并表述为「禁止反向依赖 app/components/context/features」，但 depcruise 实为两条规则：`lib-does-not-depend-on-ui` 只禁 app/components/context，**lib 允许依赖 features** | §2 拆开两条规则，注明 lib→features 合法 |
| 2 | 中 | 「`features/<域>/` = model/ + store/ + transport/」是理想化三件套：`store/` 仅 settings 有，`transport/` 仅 chat 有，各域形态异构 | §1/§3 改为「model/ 公共核心，store/、transport/ 按需」，并列出当前 7 个域 |
| 3 | 低 | 「`next.config.mjs`」文件名有误，实际为 `next.config.js` | §5 更正 |
| 4 | 低 | `contracts/generated/` 有两条生成线（api.ts ← openapi-typescript；turn-protocol.ts ← json-schema-to-typescript），原文只写前者，漏掉 v2 turn 协议契约来源 | §3/§5 补齐 |
| 5 | 低 | 目录表遗漏 4 个真实顶层项：`eslint/`（自研 i18n 规则插件）、`proxy.ts`（Next 代理层）、`types/`、`vendor/` | §3 补行 |
| 6 | 低 | Node 注意事项过窄且含机器特定路径（`~/.local/bin`）；Node 24 实测直接通过 | §6 改为「Node ≥22」 |
| 7 | 低 | 「当前 1059 例」为会漂移的快照数字 | §5 标注实测日期（2026-09-04 实测 1059 例全过） |
| 8 | 补充 | `proxy.ts` 的架构角色（后端改写 + 登录门禁）在文档中缺位 | §3/§4 补充 |
| 9 | 补充 | features/chat 为最大域未点名 | §3 点名 |

第二轮评审（同日，复核第一轮修复与新 §7，逐条实测）：

| # | 严重度 | 问题 | 修正 |
|---|---|---|---|
| 10 | 中 | §7.1 称 components/ui「待清理、新代码应使用 shared/ui/」是无依据的建议性表述：实测 `components/ui/` 仍被 13 个文件活跃引用，仓库中无收敛守护 | §7.1 改为事实陈述，「收敛」标注为评审建议 |
| 11 | 低 | §7.1 称「Button/Tooltip 各有两份实现」——Tooltip 实为**三**份（`components/common/` 6 处、`shared/ui/` 经 barrel、`components/ui/` 1 处） | §7.1 更新重复清单及各份使用面 |
| 12 | 低 | §7.1「25 个域子目录」不准：25 个子目录含 common/layout/ui 三个基础设施目录 | §7.1 改为「25 个子目录」并注明三者 |
| 13 | 低 | §7.5 预览分发描述不完整：Office 二进制浏览器不可原生渲染，PreviewKind 另有 office-text/fallback 回退 | §7.5 补回退路径 |
| 14 | 补充 | §7.1 漏掉 `shared/ui/activity-state.ts`——它是 §2 分层规则的活例子（纯类型契约放 shared/，lib 侧纯逻辑可引用且仅此一份） | §7.1 补充 |

## 目录

1. [技术栈](#1-技术栈)
2. [分层架构与依赖规则](#2-分层架构与依赖规则)
3. [目录约定](#3-目录约定)
4. [路由组织](#4-路由组织)
5. [关键机制](#5-关键机制)
6. [质量工具链](#6-质量工具链)
7. [UI 框架详细分析](#7-ui-框架详细分析)

## 1. 技术栈

| 层面 | 选型 |
|------|------|
| 框架 | Next.js App Router（可选 Turbopack：`npm run dev:turbo`） |
| UI | React 19，无重型组件库，组件自研 |
| 样式 | Tailwind CSS + CSS 变量设计令牌（`globals.css`） |
| 状态 | React Context（`context/`）+ 领域内状态模块（如 `features/settings/store/`；chat 为外部 store + selector 订阅） |
| i18n | i18next + react-i18next（en 全量打包、zh 懒加载，英文原文即键） |
| 可视化 | cytoscape + cytoscape-dagre、mermaid、chart.js、react-markdown（GFM/KaTeX/代码高亮） |
| 文档预览 | pdfjs-dist、epubjs、docx-preview、exceljs |

## 2. 分层架构与依赖规则

依赖方向由 `.dependency-cruiser.cjs` 强制（`npm run architecture:check`，扫描 app/components/context/contracts/features/hooks/lib/shared 八个顶层目录）：

```
contracts/   API 契约叶子层 —— 禁止依赖其它层
shared/      通用基础设施 —— 禁止依赖 app/components/context/features
lib/         无 UI 通用逻辑 —— 禁止依赖 app/components/context；允许依赖 features
features/    领域逻辑 —— model/store/transport 子目录禁止 import app/components/context
             （features 自己的 components/ 可渲染 UI，不受此限）
app/ components/ context/ hooks/   UI 层
```

四条分层规则名对应：`contracts-are-leaves`、`shared-does-not-depend-up`、`lib-does-not-depend-on-ui`、`feature-domain-does-not-render`。注意 shared 与 lib 的限制范围不同——lib→features 是合法方向（如 `lib/session-api.ts` 引 `features/chat/model/protocol` 的类型）。

另有两条全局规则：禁止循环依赖（`no-circular`）、任何模块禁止 import 路由页（`no-route-page-imports`）。

## 3. 目录约定

| 路径 | 职责 |
|------|------|
| `app/` | 路由页与布局（Route Groups 分组） |
| `proxy.ts` | Next 16 代理层（请求第一跳，见 §4） |
| `components/` | 跨领域通用组件 |
| `features/<域>/` | 领域模块。`model/`（纯逻辑/IR）是各域公共核心；`store/`（状态）、`transport/`（API 封装）按需存在——当前仅 settings 有 `store/`、chat 有 `transport/`，其余域形态各异（chat 另有 components/controllers/dag/messages/trace，knowledge 用 `api/`，co-writer 用 `storage/`，capabilities/runtime-status 为扁平文件，multi-user 仅 api.ts/types.ts + components/）。当前 7 个域：chat（最大域）、co-writer、knowledge、settings、capabilities、runtime-status、multi-user |
| `contracts/` | 后端契约类型（`generated/`），由 `contracts:check` 守护同步。两条生成线：`schema/openapi.json` → openapi-typescript → `api.ts`；`schema/turn-protocol.json` → json-schema-to-typescript → `turn-protocol.ts` |
| `lib/` | 无 UI 的通用逻辑与 API 客户端（如 `lib/api.ts`、`lib/stream.ts`） |
| `shared/` | `api/ auth/ storage/ ui/` 基础设施 |
| `context/` | 跨页面 React Context（AppShell、Reading、Quiz 等） |
| `hooks/` | 通用 React hooks |
| `i18n/` | i18next 初始化与 Provider（`init.ts`、`I18nProvider.tsx`） |
| `locales/{en,zh}/` | 翻译资源；`scripts/i18n_parity.mjs` + `i18n_audit.mjs` 守护 |
| `eslint/` | 自研 ESLint 规则插件（`i18n-plugin.mjs` 的 `no-literal-ui-text`：JSX 文本与 title/placeholder/alt/aria-label 禁止裸字面量，强制走 `t()`） |
| `scripts/` | 构建/检查脚本（dev、build、typecheck、测试跑批、性能预算） |
| `tests/` | Playwright E2E 与 node 测试 |
| `types/` | 全局类型声明 |
| `vendor/` | 内联维护的第三方资源（`thinking-orbs`） |

## 4. 路由组织

`app/` 下按 Route Groups 划分，每组自带 `layout.tsx`：

| Group | 用途 |
|-------|------|
| `(auth)` | 登录 / 注册 |
| `(workspace)` | 主工作区：chat、reading、books、partners、co-writer、whisper |
| `(utility)` | 工具页：settings、knowledge-bases、memory、mastery、notebooks、space、profile 等 |
| `(admin)` | 管理后台 |
| `api/` | Next API 代理（对接 Python 后端） |

所有请求先过根目录 `proxy.ts`（Next 16 代理层，前身 middleware）：把后端相对路径 `/api/*`、`/ws/*` 整体改写到 API base（地址知识收敛在入口与 `system.json`，不进前端 bundle），并处理 codex 回调改写、已下线页面 404；多用户模式下承担登录门禁（校验 cookie token、失效重定向登录页，静态资源与 auth 页豁免）。大体积上传走专用 App Router 路由直连 FastAPI，刻意绕过代理以免 body 被克隆限容。

## 5. 关键机制

- **配置单一真源**：`next.config.js` 启动时读取 `../data/user/settings/*.json`（backend_port、api_base、auth 开关）；环境变量仅作 Docker/CI 显式覆盖。应用版本从 `deepmentor/__version__.py` 正则解析，避免双源漂移。
- **契约驱动类型**：`contracts/schema/` 下的 `openapi.json` 与 `turn-protocol.json` 分别经 openapi-typescript、json-schema-to-typescript 生成 `contracts/generated/`（`api.ts`、`turn-protocol.ts`），前端不得手写 API 类型。
- **领域模块可测性**：`features/<域>/model` 为纯 TypeScript（无 DOM 依赖），可在 Node 下直接测试（`test:node`，截至 2026-09-04 实测 1059 例全过）。
- **i18n 守护**：`keySeparator: false`（英文原文即键）；en 打进首屏包、zh 经 `ensureLanguage` 动态 import；`i18n:check` = parity（en/zh 键集一致）+ audit（t() 字面量均有条目、UI 字面量扫描）。
- **性能预算**：`scripts/route_budgets.mjs` 对构建产物按路由设体积上限（`npm run perf:check`）。

## 6. 质量工具链

```
npm run check:fast
  = contracts:check → architecture:check → typecheck
    → test:node → test:unit(vitest) → lint(eslint) → i18n:check
npm run check      # check:fast + next build + perf:check
npm run audit      # Playwright UI 审计
npm run test:e2e:critical / test:e2e:multi-worker
```

注意：Node 版本敏感——`test:node` 需 Node ≥22（测试代码依赖 `require(esm)`，Node 18 会假性失败；Node 24 已实测通过）。

## 7. UI 框架详细分析

> 本节为 2026-09-04 补充，全部结论逐条对照 `web/` 源码核实；路径均相对 `web/`。

### 7.1 组件体系：无第三方组件库，三层自研

不引入 Radix/AntD 等组件库，UI 完全自研，分三层：

| 层 | 位置 | 内容 |
|---|---|---|
| 设计原语 | `shared/ui/` | 9 个可复用原语：Button、IconButton、Dialog、Field、InlineAlert、StatusChip、Tooltip、Skeleton、EmptyState + `styles.ts`（`cn()` = clsx + tailwind-merge，全项目类名合并唯一入口）。`activity-state.ts` 是层间契约范例：`ActivityState` 四态纯类型放 shared/，让 `lib/` 侧纯逻辑（`lib/book-activity.ts`）可引用且全仓仅此一份 |
| 通用构件 | `components/common/` | 17 个组件（Markdown 渲染族三档、Modal、PickerShell/PickerHeader、ToastViewport、RichCodeBlock、ProviderIcon/BrandIcon 等）+ 4 个渲染管线支撑文件（markdown-renderer-core/types、code-block-themes、prism 主题声明） |
| 业务组件 | `components/`（25 个子目录 + 5 个顶层组件，共 271 个 tsx；common/layout/ui 三者为基础设施目录）+ `features/<域>/` 内部组件（51 个 tsx） | 聊天、阅读、知识库等业务界面；顶层组件为 Mermaid、Geogebra、ThemeScript、UserAvatar、SessionList |

注意：同名组件存在多份并行实现——Tooltip 三份（`components/common/` 6 处使用、`shared/ui/` 经 `index.ts` barrel 导出、`components/ui/` 1 处），Button 两份（`components/ui/` 4 处、`shared/ui/`）。`components/ui/`（Button、ConfirmDialog、Tooltip）仍被 13 个文件活跃引用，仓库中没有强制收敛的守护（无 lint/depcruise 规则）。评审建议：新代码优先 `shared/ui/`，逐步收敛同名原语——此为建议而非既成约定（评审记录 #10）。

### 7.2 设计令牌与主题系统

- **颜色全部令牌化**：`globals.css`（约 1100 行）定义 CSS 变量，`tailwind.config.js` 把 `background/foreground/card/popover/primary/secondary/muted/accent/destructive/border/input/ring/success/warning/info` 等逐个映射为 Tailwind 颜色——组件里写 `bg-background`、`text-muted-foreground`，即是在消费变量，不写死色值。`--overlay` 统一弹层遮罩色（取代各组件硬编码的 `bg-black/40`）。
- **4 套主题族**：`.theme-snow`（默认，纯白 + 蓝强调）、`:root` Cream（暖白 + 陶土）、`.dark`（暖近黑 + 陶土）、`.theme-glass`（近黑 + 半透明紫玻璃）。Tailwind `darkMode: "class"`，主题类挂在 `<html>`；每套主题必须发布全部令牌（见 `globals.css` 头部注释的令牌清单）。
- **防主题闪烁**：`components/ThemeScript.tsx` 是 Server Component，把恢复逻辑内联进 SSR HTML——hydration 前就从 localStorage（`deepmentor-theme`）恢复主题类；无存储偏好时跟随 `prefers-color-scheme` 并回写。
- **中英混排字体确定性**：Geist/Lora 只覆盖拉丁文，Tailwind 字体栈在其后显式列 CJK face（PingFang SC / 宋体系 / Noto 系列），保证 serif 标题在任意机器上都是「Lora + 宋体」，而非浏览器随机兜底。

### 7.3 布局外壳与响应式

- **AppShell**（`components/layout/AppShell.tsx`，(workspace)/(utility) 两组路由共用）：≥768px 侧栏与内容 flex 并排；<768px 侧栏脱离文档流，变为遮罩抽屉 + 顶栏开关；路由切换自动收起（`useSidebarDrawer()`）。
- **响应式铁律：静态半边归 CSS，有状态半边才归 JS**——布局分叉用 `max-md:`/`md:`/`lg:` 表达，SSR 首帧在手机上即正确；JS（`hooks/useDevice.ts`）只管抽屉开合这类状态。`useDevice()` 基于 `useSyncExternalStore`，三级断点 mobile <768 / tablet 768–1023 / desktop ≥1024 **刻意对齐 Tailwind md/lg**（改一边必须同步另一边），`isCompact`（= mobile‖tablet）是面板代码的「放不放得下第二列」判据。
- **右侧面板互斥**：FilePreviewDrawer、SessionViewerPanel、SessionDagPanel 同一时刻至多打开一个（后者见 [chat-session-dag.md](./chat-session-dag.md) 评审#10）。

### 7.4 弹层、交互模式与无障碍

- **弹层家族统一根**：`PickerShell` 是所有浮层的外壳（ref-count 管理 `data-picker-open` body 标记，打开期间冻结背景脉冲动画——backdrop-blur 会把背景动画重采样成闪烁）；通用 `Modal` 构建其上；9+ 业务弹层（7 个 chat 选择器 + 笔记本/伙伴弹层）直接复用。
- **无障碍内建在原语里**：Dialog 自带焦点管理（`data-autofocus` 定位初始焦点、关闭后焦点还原、Escape 关闭且 busy 时抑制）；Button 有 focus-visible 令牌环、`aria-busy`、loading 图标 `aria-hidden`；Toast 视口 `role="status"` + `aria-live="polite"`。
- **动效克制**：framer-motion 仅 4 个文件使用，且 PickerShell 用 `useReducedMotion` 尊重系统「减弱动态效果」偏好。

### 7.5 富内容渲染管线

- **Markdown 三档渲染器**：Simple / 默认 / Rich（`components/common/`），共享同一 props 契约：`variant: default|compact|prose|trace` + 功能开关（`enableMath/enableCode/enableMermaid/enableImages/allowHtml`）+ `trackSourceLines`（输出源码行标记，供编辑器预览同步滚动）。
- **Rich 档按需加载**：remark-math + rehype-katex 动态 import；Mermaid 与 RichCodeBlock 均 `dynamic()`；```` ```flow ```` / ```` ```sequence ```` 围栏自动转 mermaid 图渲染；Mermaid 主题变化时重跑 `initialize` + 重渲染。
- **代码高亮**：react-syntax-highlighter（Prism），主题集统一收口在 `code-block-themes.ts`。
- **可视化组件**：`components/Mermaid.tsx`（图）、`components/Geogebra.tsx`（交互几何）、react-chartjs-2（图表）、cytoscape（session DAG）。
- **文档预览**：`components/chat/preview/` 的 `previewerFor.ts` 把附件映射为 `PreviewKind`（pdf/image/svg/markdown/code/text/docx/xlsx/office-text/fallback），FilePreviewDrawer 据此动态 import 对应预览器（pdfjs-dist / epubjs / docx-preview / exceljs）。Office 二进制浏览器无法原生渲染：docx/xlsx 走专用预览器，pptx 等回退到提取器纯文本（office-text）或仅下载（fallback）。

### 7.6 图标与品牌资产

- 界面图标统一 lucide-react（tree-shakeable 单组件 import）。
- 品牌/提供商图标为 `public/` 下静态资产：`agent-icons/`（各 LLM 厂商）、`knowledge-engine-icons/`（GraphRAG/LightRAG/LlamaIndex 等），经 `ProviderIcon`/`BrandIcon` 组件消费；`scripts/build-brand-icons.mts` 维护资产生成。
