# 顶部 Banner(Logo / 菜单 / 头像)UI 架构设计方案

> 状态:**草案,供决策**。2026-09-04。所有现状描述均对照 `web/` 源码核实。
> 相关文档:[frontend-architecture.md](../frontend-architecture.md) §7(UI 框架)、[chat-session-dag.md](../chat-session-dag.md)(面板互斥先例)。

## 评审修正记录(2026-09-05,逐条对照代码核实)

| # | 严重度 | 问题 | 修正 |
|---|---|---|---|
| R1 | **高** | 方案未盘点右侧三面板的覆盖行为:`FilePreviewDrawer`/`SessionViewerPanel`(SessionDagPanel 同模式)是 `fixed top-0 z-[30] h-dvh` **全视口覆盖**(现状连侧栏也盖住),与方案给 Banner 的 z-30 同层冲突,「面板打开时盖 Banner 还是让位」完全没讨论 | §4.1 明确:面板保持全高覆盖语义,z 提至 z-40;Banner 保持 z-30 常驻可见性让位面板 |
| R2 | 中 | 账户区物料清单不完整:UtilitySidebar footer 实为 ProfileLink + **AdminLink + LogoutButton**(漏列后两者);WorkspaceSidebar footer 仅 ProfileLink | §4.2/§4.3 更新迁移清单,右侧聚合为头像下拉(Profile/Admin/Logout) |
| R3 | 中 | 只说「复用排序数据」,未定义水平菜单下**拖拽重排的编辑 UX**——SidebarNav 移除后编辑界面随 nav 消失 | 新增决策点 D7 |
| R4 | 低 | D2 理由不完整:`SidebarNav` 注释明确「collapsed rail 是折叠项(More)的入口」,导航上移后该语义消失 | D2 补注语义变化 |
| R5 | 低 | 性能结论过粗:route_budgets 确按跨路由交集扣除公共 shell(`intersection` 逻辑已核实),「预算不受影响」仅指预算数字;绝对传输量仍增加 | §6 精确化 |
| R6 | 低 | z-30 值在全仓已有 14 处使用(含两个右面板 z-[30]),Banner 直用 z-30 语义模糊 | 并入 R1 的分层说明 |
| R7 | 补充 | 移动端高度变化:旧顶条 h-11(44px)→ Banner h-14(56px),竖向空间 -12px,composer 位置需回归 | §6 补风险 |
| R8 | 核实通过 | 「More」i18n 键存在(en/zh app.json:3159);两组侧栏均走 `SidebarShell`(utility 侧栏也挂会话列表),「动一处」的判断成立 | — |

第三轮评审（2026-09-05，针对 §9 细化设计，逐条对照 rebrand 后代码）：

| # | 严重度 | 问题 | 修正 |
|---|---|---|---|
| R9 | 中 | §2/§4 现状描述在 rebrand 提交后过时：侧栏 Docs/GitHub/VersionBadge 已删除、SessionDagPanel 实为内联面板、PRIMARY_NAV 变 7 项 | §9.1 勘误，D3 收敛为「首期右侧仅账户区」 |
| R10 | 低 | zh「Research」现译「研究」，与用户决策用词「科研」及既定术语「科研智能体」不一致；该键另被 notebook/能力页两处使用 | 统一改为「科研」，三处使用语义兼容（均已核实） |
| R11 | **高** | **移动端导航可达性**：若按 §4 字面实施（侧栏去掉全部导航），移动端唯一导航入口是侧栏抽屉——抽屉将没有任何功能菜单，手机用户无法到达 Book/Reading 等页面 | §9.3 补充：抽屉内渲染 `TopNavList`（`md:hidden` 三组纵向列表），与桌面顶栏菜单同源 |
| R12 | 低 | Learning Agent 点击语义：原侧栏点击重置为新会话（`onNewChat` + `router.push`）；顶栏改普通 Link 后，`/chat` 精确路径点击为 no-op（本就是新会话页），`/chat/<id>` 点击导航到 `/chat` 即新会话——语义等价 | 接受简化，不再传 onNewChat |
| R13 | 低 | 组内排序消费 `sidebar-layout` 的时序：SSR 首帧按声明序渲染，hydration 后按已存排序调整——下拉默认收起，无可见抖动；lib→components 方向合法（§2） | 接受；客户端 mount 后读取 |

## 1. 需求

在现有「左(侧栏)— 中(内容)— 右(面板)」架构基础上增加一个顶部 Banner:

- 居左:产品 LOGO
- 居中:菜单栏
- 居右:用户 Avatar 图标

## 2. 现状盘点(设计约束的来源)

| # | 现状 | 对本设计的影响 |
|---|---|---|
| 1 | `AppShell`(`components/layout/AppShell.tsx`)为 `flex h-dvh` 单行结构:`[sidebar | main]`,**桌面端没有任何顶栏** | Banner 需要新增一行,Shell 从单行变两行 |
| 2 | LOGO 现在位于侧栏头部(`SidebarShell` collapsed 60px rail / expanded 220px 两种形态,expanded 是 `logo.png + banner.png` wordmark) | 顶栏接管 LOGO 后,侧栏头部需移除或改作他用,否则双 LOGO |
| 3 | 头像 = `ProfileLink`(侧栏 footer),**仅在多用户 auth 开启且已登录时渲染**;单用户模式下侧栏 footer 只有 Docs / GitHub / VersionBadge | 顶栏右侧在单用户模式下是空的,需要决定放什么 |
| 4 | 菜单 = `nav-entries.ts` 的 `PRIMARY_NAV`(8 项)+ `SECONDARY_NAV`(Memory / Knowledge Center / Settings,注释明确「不可折叠」);**用户可自定义排序、可折叠进「More」**(`lib/sidebar-layout.ts`,localStorage `deepmentor.sidebar.navLayout`,纯函数 SSR-safe) | 顶栏菜单应复用同一数据源与排序/折叠逻辑,而不是另起一套 |
| 5 | 导航项带能力门控(`requires: Capability`,如 `llm`,无能力锁定) | 顶栏菜单项必须继承同一门控 |
| 6 | 移动端(<768px)main 内已有 h-11 顶条:汉堡按钮 + LOGO;侧栏变 z-50 抽屉 + z-40 scrim | 新 Banner 应与这条移动顶条**合并**,不能并存两条 |
| 7 | 响应式铁律:静态分叉用 CSS(`max-md:`/`md:`/`lg:`),JS 只管有状态部分(抽屉开合、`useDevice`) | Banner 的显示/隐藏分叉必须延续此模式,保证 SSR 首帧正确 |
| 8 | 侧栏 aside 用 `h-dvh` 定高 | Shell 加一行后,侧栏高度必须改为随行内拉伸(`h-full`),否则溢出 |
| 9 | z-index 现状:scrim z-40、侧栏抽屉 z-50;`PickerShell` 打开时给 body 标记 `data-picker-open` 冻结背景动画 | Banner 层级须低于抽屉/scrim;glass 主题下 banner 若用 backdrop-blur 需测与该机制的相互作用 |
| 10 | `(workspace)` 与 `(utility)` 两组路由共用 AppShell(sidebar 以 props 注入);`(admin)` 自有极简布局、`(auth)` 无 Shell | Banner 归属 AppShell 即覆盖两组;admin/auth 是否接入是独立决策 |
| 11 | 性能预算 `route_budgets.mjs` 按路由扣除「框架 + 公共根 shell」后再比对 | Banner 放在公共 shell 层,对各路由预算影响有限;菜单图标须 lucide 单组件 import 保持 tree-shaking |
| 12 | i18n:英文原文即键,en/zh 成对新增,`i18n:check` 守护;Playwright `ui-audit` / `critical-turns` / `multi-worker`(含移动视口)E2E | 新文案成对加键;布局改动会触及 E2E 选择器与响应式审计 |

## 3. 方案对比

### 方案 A:品牌条(最小改动)

顶栏仅含 LOGO + 头像(+ 全局工具),**不放菜单**;导航仍留侧栏。

- 优点:改动最小,不动导航体系与个性化逻辑;水平空间零压力。
- 缺点:不满足「菜单栏居中」的需求本体;顶栏存在感弱,信息重复度低但增量也低。

### 方案 B:主导航全部上顶栏,侧栏降级为上下文栏 ⭐ 推荐

顶栏 = LOGO + **水平化主导航**(PRIMARY_NAV,复用排序/折叠/More 逻辑)+ 头像;侧栏去掉头部 LOGO 与导航,收缩为「上下文栏」(chat 会话列表、域内子导航),保留 collapsed rail 能力。

- 优点:精确满足需求;消除「侧栏导航 + 顶栏菜单」双入口的维护分叉;`sidebar-layout.ts` 的排序/折叠数据直接复用,用户无感知迁移;侧栏腾出的空间还给上下文内容(会话列表不再和 8 个功能项挤一列)。
- 缺点/风险:8 个功能项在 768–1024px 宽度放不下(见 §5-D1);侧栏语义变化波及 E2E 与用户习惯;改动面最大。

### 方案 C:域切换条(折中)

顶栏菜单只放**顶层域**(如 Chat / Learning / Consoles / Admin 的切换或下拉),PRIMARY_NAV 细项仍留侧栏。

- 优点:菜单居中且有信息量;水平空间压力小;侧栏基本不动。
- 缺点:「域」划分是新产品概念,需要额外定义;当前二组路由(workspace/utility)与菜单域不完全对齐,有概念重叠。

**推荐:方案 B**。它与「菜单栏居中」的诉求一一对应,复用面最大(导航数据、排序、门控、i18n 全部现成),且顺势解决侧栏「导航 + 会话列表」拥挤的历史问题。以下展开方案 B;若决策选 A/C,§4 的 Shell 结构改动仍然适用,只裁剪 TopNav 部分。

## 4. 方案 B 架构改动清单

### 4.1 AppShell 结构(核心改动)

```
现状:  <div flex h-dvh>  [sidebar | main[移动顶条, children]]
目标:  <div flex h-dvh flex-col>
         ├─ <TopBanner>                      ← 新增,整宽,h-14 shrink-0,z-30
         └─ <div flex min-h-0 flex-1>
              ├─ [scrim z-40]                ← 原样
              ├─ [sidebar 抽屉 z-50]          ← 原样,aside 高度 h-dvh → h-full
              └─ <main>[children]            ← 移动顶条删除,汉堡移入 TopBanner
```

要点:

- Banner 放 AppShell 层而非各 layout,`(workspace)`/`(utility)` 自动共享;菜单内容经 props/slot 注入(`bannerNav={<WorkspaceTopNav />}`),与现有 `sidebar={...}` 注入模式一致。
- 侧栏 `SidebarShell` 的 `aside` 定高从 `h-dvh` 改 `h-full`(自拉伸)——**漏改会溢出 56px**,这是本改动最容易踩的坑。
- 移动端:现有 h-11 顶条(`md:hidden`)整体删除,汉堡按钮并入 TopBanner 左端(`max-md:` 显示);抽屉仍是 `max-md:fixed inset-y-0 z-50`,覆盖 Banner,行为不变。
- **右侧面板与 Banner 的层级关系(评审 R1)**:三个右面板(FilePreviewDrawer / SessionViewerPanel / SessionDagPanel)现为 `fixed top-0 z-[30] h-dvh` **全视口覆盖**——现状连侧栏都被盖住,「面板即全屏抽屉」是既有语义。方案决策:**面板保持全高覆盖,其 z 提至 `z-40`**(与 scrim 同层语义),打开时覆盖 Banner 与侧栏,行为与现状一致;Banner(`z-30`)仅在有面板打开时被覆盖,常驻可见。备选「面板改为 `top-14` 让 Banner 常驻」改动大、且破坏「面板 = 全屏抽屉」的一致性,不推荐首期。全仓 z-30/z-[30] 已有 14 处使用,根层浮动元素统一收在 ≥ z-40,嵌套内容不受影响。

### 4.2 新组件 `components/layout/TopBanner.tsx`

```
[☰(仅 md 以下)] [logo.png + banner.png wordmark]   [TopNav 居中]   [ProfileLink·头像 | Docs·GitHub]
     左:品牌区                      中:菜单区                        右:账户区
```

- **左**:LOGO 迁自 `SidebarShell` 头部,`Link href="/"`,行为与现侧栏一致。
- **中**:`TopNav` —— 数据源就是 `PRIMARY_NAV` + `lib/sidebar-layout.ts`(`resolveNavLayout` 给出 `visible/collapsed`),渲染为水平项;`collapsed`(用户折叠进 More 的)与放不下的项收进「More」下拉(复用 `PickerShell`/现有 Dropdown 能力);`requires` 能力门控沿用(锁定项渲染为禁用态,与侧栏现行为一致)。用户已有的排序在顶栏原样生效,无迁移成本。
- **右**:账户区聚合为**头像下拉**(评审 R2):`UserAvatar` 作锚点,下拉内聚合 Profile / Admin / Logout(`ProfileLink`、`AdminLink`、`LogoutButton` 迁自两组侧栏 footer;单用户模式下仅 ProfileLink 条件渲染,现状逻辑不变)。下拉之外常驻 Docs / GitHub 图标。单用户模式下的占位见 §5-D3。
- 高度 `h-14`(56px)令牌化:背景 `var(--background)` 或 `var(--card)`、下边框 `var(--border)`,全部现有令牌,无新令牌需求;glass 主题下如需半透明,须回归 PickerShell 冻结动画机制(§2-#9)。

### 4.3 SidebarShell / 侧栏改造

- 删除头部 LOGO 区(collapsed 形态的 logo 位改为展开按钮,expanded 形态头部整行移除或改为上下文标题)。
- footer 物料**全部迁移**(评审 R2 补全清单):WorkspaceSidebar footerSlot = ProfileLink;UtilitySidebar footerSlot = ProfileLink + **AdminLink + LogoutButton**;SidebarShell 自带 Docs / GitHub / VersionBadge。去向:Profile/Admin/Logout 聚合进顶栏头像下拉,Docs/GitHub 迁顶栏右侧,VersionBadge 留侧栏底部。两组侧栏均走 `SidebarShell` 且 utility 侧栏同样挂会话列表(`showSessions`)——改造收口在 `SidebarShell` 一处 + 两个 footerSlot。
- 主体保留:chat 域的会话列表、courses / mastery / reading 集合等上下文内容不变;`PRIMARY_NAV`/`SECONDARY_NAV` 从侧栏移除(SECONDARY_NAV 去向见 §5-D6)。
- collapsed rail(60px)保留能力,但**语义双重变化**(评审 R4):从「精简导航」变为「纯上下文栏的收纳态」,且现状 rail 是折叠项(More)的入口(源码注释明示)——该入口职责随导航上移转给顶栏 More 下拉,见 §5-D2/§5-D7。

### 4.4 路由组接入

| 组 | 接入方式 |
|---|---|
| `(workspace)` | AppShell 默认带 Banner,`bannerNav` 注入 PRIMARY_NAV |
| `(utility)` | 同上(菜单可复用同一 TopNav;两组菜单差异见 §5-D6) |
| `(admin)` | 现无 AppShell;是否补 `min-h-screen` + Banner 见 §5-D4 |
| `(auth)` | 登录/注册页不接入(保持无框架) |

## 5. 决策点(需要拍板)

| # | 决策点 | 选项 | 建议 |
|---|---|---|---|
| D1 | **水平空间放不下 8 项怎么办** | a) `lg+` 图标+文字、`md–lg` 仅图标、溢出进 More;b) 始终只显示用户排序前 N 项,其余进 More | a+b 混合:断点降级 + More 兜底;`md–lg`(平板竖屏)本就是「放不下第二列」的紧凑档 |
| D2 | **collapsed rail 去留** | a) 保留(收纳态);b) 移除,侧栏只有开/关 | 保留,实现成本为零,阅读页等已有用户习惯 |
| D3 | **单用户模式下顶栏右侧放什么**(ProfileLink 登录后才渲染) | a) 留空;b) 主题切换器迁入;c) Docs/GitHub 迁入兜底 | c+b:右侧永不空,且与登录态无关 |
| D4 | **admin 组是否接入 Banner** | a) 接入(管理后台获得一致外壳);b) 保持独立 | 先 b,admin 面向低频运维,后补无阻塞 |
| D5 | **沉浸/专注场景是否隐藏 Banner** | a) 不隐藏;b) 路由级声明式隐藏(如 watching / chat 专注模式) | 预留 b 的机制(布局 data-attribute),首期不做 |
| D6 | **SECONDARY_NAV(Memory/Knowledge/Settings)去向** | a) 一并上顶栏(并入 More);b) 留侧栏底部 | b:源码注释明确其「必须可找到、不可折叠」定位,留侧栏底部与现状一致;utility 组菜单 = 同一 TopNav(两组本就共享 nav-entries) |
| D7 | **导航自定义(拖拽排序/折叠)的编辑入口**(评审 R3) | a) 顶栏 More 下拉内嵌「自定义导航」管理面板;b) 首期顶栏只读消费已存排序,编辑 UI 随侧栏 SidebarNav 保留一个入口(如侧栏底部「管理导航」) | 首期 b:水平条内做拖拽成本高、收益低;先把「消费排序」做对,编辑入口收进侧栏底部小按钮,后续再评估搬进 More |

## 6. 不变量与风险

- **响应式铁律不破**:Banner 的断点行为全部 CSS(`max-md:`/`md:`/`lg:`),JS 只保留抽屉开合;SSR 首帧正确性不受影响。
- **`h-dvh` 高度预算**:Banner 占 56px 后,内容区 `min-h-0 flex-1` 结构不变;iOS Safari dvh 注释(现 AppShell 头部)仍适用。**移动端竖向空间净减 12px**(旧顶条 44px → Banner 56px,评审 R7):手机上聊天区与 composer 的可视高度变化需在回归中确认。
- **导航个性化兼容**:`sidebar-layout.ts` 纯函数与 localStorage 键原样复用,老用户的排序/折叠在顶栏直接生效,无需迁移;但**编辑 UI 的去向**是开放决策(D7)。
- **性能(评审 R5 精确化)**:已核实 `route_budgets.mjs` 按跨路由 chunk **交集**识别公共根 shell——Banner 代码进公共 shell 后,**各路由预算数字不变**;但每条路由的绝对传输量仍小幅增加,以 lucide 单组件 import + 无新依赖控制在 KB 级。
- **i18n / 测试**:「More」键已存在(en/zh app.json:3159);新文案(如「自定义导航」)en/zh 成对;`ui-audit`、`critical-turns`、multi-worker(含移动视口)E2E 与响应式审计需回归——侧栏选择器、移动顶条选择器会变。
- **回滚**:TopBanner 为新增文件,AppShell/SidebarShell 改动集中(结构行 + 头部/footer 迁移),整体可单 commit 回滚,无数据迁移(localStorage 键不变)。

## 7. 分期实施建议

1. **Phase 1(骨架)**:AppShell 两行化 + TopBanner(LOGO + 右侧账户区)+ 移动顶条合并;侧栏暂不动(此时双 LOGO,仅内部预览)。
2. **Phase 2(菜单)**:TopNav 接入 PRIMARY_NAV + sidebar-layout 排序/More + 能力门控;SidebarShell 移除导航与 LOGO、footer 迁移。
3. **Phase 3(收尾)**:D3/D5 决策落地、admin 取舍、E2E/ui-audit 回归修复、glass 主题与性能预算复核。

## 8. 验证清单(实施后)

- [ ] 桌面 ≥1024 / 平板 768–1023 / 手机 <768 三档截图对照;SSR 首帧(禁 JS)手机端 Banner 正确
- [ ] More 折叠项、用户自定义排序在顶栏生效;能力锁定项禁用态正确
- [ ] 单用户 / 多用户登录 / 未登录三态下顶栏右侧表现符合 D3 决策
- [ ] 抽屉/scrim/浮层(Z 序)不因 Banner 改变;**三个右面板打开时全高覆盖 Banner(z-40)、关闭后 Banner 恢复可见(评审 R1)**;PickerShell 冻结动画机制在 glass 主题下正常
- [ ] 导航排序在顶栏生效;**D7 编辑入口**可完成一次拖拽排序 + 折叠/展开
- [ ] `npm run check:fast` + `perf:check` 预算不回退;`test:e2e:critical` / `multi-worker` 通过

## 9. 细化设计(2026-09-05,D1 已决策,方案 B 进入实施)

> **决策(D1)**:顶部菜单采用**三组下拉**结构,替代「8 项平铺 + More 兜底」;其余决策点按本文推荐意见执行(B 方案、D2 保留 rail、D4 admin 暂不接入、D5 预留机制首期不做、D6 SECONDARY_NAV 留侧栏底部、D7 首期只读)。冲突处以本节为准。

### 9.1 现状勘误(rebrand 提交 `15a15a34` 之后,修正前文两处过时描述)

- 侧栏 footer 的 Docs / GitHub / VersionBadge **已被 rebrand 移除**,footer 仅剩 footerSlot(ProfileLink + AdminLink + LogoutButton)——§4.2/§4.3 与 D3 相应收敛:顶栏右侧只做**账户区**(头像下拉),单用户模式下条件渲染为空(D3 首期不补占位,后续可加主题切换)。
- SessionDagPanel 实为**内联面板**(`flex h-full`,由 ChatWorkspace 在面板层挂载),不是 fixed 覆盖层;`fixed top-0 z-[30] h-dvh` 的只有 FilePreviewDrawer 与 SessionViewerPanel 两个——R1 的 z-40 提升只涉及这两个文件。
- 品牌已改为「学研助手」,侧栏头部为 logo.png(`?v=naval-20260904`)+ 文字 wordmark;PRIMARY_NAV 现为 7 项(Partners/My Agents/Courses 暂时隐藏),其中 `/research-agent` Research Agent(科研智能体,`Microscope` 图标,无能力门控)为新增。
- EduHub 的落地页是 `web/app/(utility)/space/skills/page.tsx`(`SkillsSection` + EduHubImportModal),即 `/space/skills` 路由。

### 9.2 顶部菜单数据结构(`nav-entries.ts`)

`NavEntry` 增加可选 `group?: "learning" | "research" | "skills"`;`PRIMARY_NAV` 各项打标(除 Research Agent 外均为 `learning`);新增:

```ts
export const SKILLS_NAV: NavEntry[] = [
  { href: "/space/skills", label: "EduHub", icon: Puzzle },   // 技能中心暂只此一项
];
export const TOP_NAV_GROUPS: TopNavGroup[] = [
  { id: "learning", label: "Learning",     entries: PRIMARY_NAV.filter(g=="learning") },
  { id: "research", label: "Research",     entries: [...Research Agent] },
  { id: "skills",   label: "Skill Center", entries: SKILLS_NAV },
];
```

- 组内顺序消费 `sidebar-layout.ts` 已存排序(`resolveNavLayout`:visible 在前、折叠项附尾),老用户排序无缝生效;**首期不提供编辑 UI**(D7,随 SidebarNav 移除,后续在 More 面板内恢复)。
- 组激活态:组内任一 `isNavActive` 即激活;`/space/skills` 需特判——它同时命中 Learning Space(`/space` 前缀)与 EduHub,此时 learning 组不激活,避免双高亮。

### 9.3 组件与交互

- **`components/layout/TopBanner.tsx`**(新增,含未导出的 NavDropdown/AccountMenu/TopNavList):
  - 左:汉堡按钮(仅 `max-md:flex`,触发抽屉)+ `Link /`(logo + wordmark,样式对齐现侧栏头部)。
  - 中:三组下拉按钮(`max-md:hidden`),`aria-haspopup`/`aria-expanded`,Escape 与点击外部关闭(组件内自实现,不新增共享 API);菜单项 = 图标 + `t(label)`,`requires` 未满足时禁用态 + 锁定 tooltip(复用现有键「Locked — contact your administrator to get access.」)。
  - 右:账户区(`md` 以上常驻)——触发器为 `UserAvatar`(`fetchAuthStatus`,未登录/单用户不渲染),面板内复用 `<ProfileLink />`(`<AdminLink />`/`<LogoutButton />` 自带权限条件渲染与整行样式)。
  - Banner 容器:`h-14 shrink-0 border-b border-[var(--border)] bg-[var(--background)] z-30`。
- **`AppShell.tsx`**:改两行;删除旧 `h-11` 移动顶条(汉堡并入 Banner);抽屉/scrim/main 移入第二行容器;aside 定高 `h-dvh` → `h-full`。
- **`SidebarShell.tsx`**:删头部 LOGO 区(expanded 头部只留折叠开关)、删 `SidebarNav` 引用;**移动抽屉内新增 `TopNavList`(`md:hidden`,三组纵向列表,组标题 + 行项,点击关抽屉)**;`SECONDARY_NAV` footer 原样保留(D6);`footerSlot` 及 `handleHomeClick`/`onNewChat` 随导航移除(Learning Agent 项为普通 Link,`/chat` 路由本身即新会话);aside `h-dvh` → `h-full`。
- **调用方**:`WorkspaceSidebar`/`UtilitySidebar` 删 footerSlot(ProfileLink/AdminLink/LogoutButton 迁 Banner)与 onNewChat;删除 `SidebarNav.tsx`(无其他消费方)。
- **Z 序(评审 R1)**:`FilePreviewDrawer`/`SessionViewerPanel` `z-[30]` → `z-40`,保持全高覆盖 Banner。
- **i18n**:新增 `"Skill Center"`(技能中心)、`"EduHub"`(EduHub,不译);zh `"Research"` 研究 → **科研**(与「科研智能体」既定术语一致,notebook/能力页三处同词联动,评审 R10)。

### 9.4 实施后验证

§8 清单适用,其中 D7 编辑入口一项按「首期不提供」豁免;另加:移动抽屉内三组导航可达、`/space/skills` 单激活、`Research` zh 文案联动三处检查。

### 9.5 实施记录(2026-09-05,已交付)

| 交付物 | 说明 |
|---|---|
| `web/components/layout/TopBanner.tsx` | 新增:`TopBanner`(品牌/三组下拉/账户区)+ 导出 `TopNavList`(抽屉纵向导航);组内排序消费 `sidebar-layout`(SSR 首帧为声明序,hydration 后生效);Escape/点击外部关闭;能力门控禁用态 + 锁定 tooltip |
| `web/components/sidebar/nav-entries.ts` | `NavEntry.group` 标记、`SKILLS_NAV`(EduHub → `/space/skills`)、`TOP_NAV_GROUPS`(learning/research/skills) |
| `web/components/layout/AppShell.tsx` | 两行化:TopBanner + [sidebar \| main];删除旧 h-11 移动顶条 |
| `web/components/sidebar/SidebarShell.tsx` | 删头部 LOGO 与 SidebarNav;抽屉内嵌 TopNavList(`md:hidden`);footerSlot/onNewChat 移除;aside `h-dvh` → `h-full` |
| `WorkspaceSidebar` / `UtilitySidebar` | 删 footerSlot(ProfileLink/AdminLink/LogoutButton 迁 Banner)与 onNewChat |
| `SidebarNav.tsx` | 删除(无消费方;`sidebar-layout.test.ts` 引用的是 lib 类型,不受影响) |
| `SessionViewerPanel` / `FilePreviewDrawer` | `z-[30]` → `z-40`(评审 R1:保持全高覆盖 Banner) |
| locales en/zh | 新增 `Skill Center`/`EduHub`;zh `Research` 研究 → 科研 |

验证:`npm run check:fast` 全绿(contracts / architecture(无循环依赖)/ typecheck / test:node 1072 例 / test:unit / lint 0 error / i18n parity+audit)。品牌字面量「学研助手」为 i18n audit 咨询性提示,与 rebrand 既有处理一致。待运行:`perf:check`、Playwright E2E(ui-audit / critical-turns / multi-worker)与三档视口人工回归。
- [ ] `npm run check:fast` + `perf:check` 预算不回退;`test:e2e:critical` / `multi-worker` 通过
