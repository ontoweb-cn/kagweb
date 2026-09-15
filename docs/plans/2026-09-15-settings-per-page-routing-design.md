# 设置页：从单文档锚点改为按功能域独立路由（设计讨论稿）

状态：**已实施**（2026-09-15；实施记录见 §9，评审与修复见 §10；决策记录见
`docs/adr/0004-settings-routed-per-category.md`）
日期：2026-09-15

## 1. 需求

当前所有设置都在一个页面里，用 `http://localhost:8092/settings#overview`、
`#llm`、`#about` 这样的片段区分功能。希望**每个功能有独立的页面**（真实路由）。

## 2. 现状：这不是疏漏，是一个已写入 ADR 的决定

现有实现是刻意设计的「单文档 + 锚点」，并在多处留下了理由：

| 位置 | 内容 |
| --- | --- |
| `docs/adr/0002-unversioned-canonical-urls.md` | 「Canonical page routes use product nouns, including `/chat/{sessionId}` and **fragment-addressed sections under `/settings`**」，并明确**拒绝**保留兼容别名与重定向 |
| `web/app/(utility)/settings/page.tsx:70-76` | 注释：「Settings is one document… Every navigator target is an anchor in this document; no duplicate leaf routes or redirect aliases remain.」 |
| `web/components/settings/SettingsNav.tsx:66-75` | 注释解释为何弃用 hub → sub-hub → leaf 三级点击，改为常驻左栏 |
| `web/tests/settings-continuous-navigation.test.ts:159-162` | 断言 `VersionBadge` **不得**出现 `/settings/about`，即测试主动锁死「没有子路由」 |

机制上：

- 只有一条路由 `/settings`。`layout.tsx` 提供 5 个 provider
  （`SettingsProvider` / `SettingsAccessProvider` / `UiSettingsProvider` /
  `ModelCatalogProvider` / `SettingsDraftProvider`），**整条设置路由共享一份状态**。
- `page.tsx` 把 8 个顶层 section 交给 `CategoryScroll` 顺序堆叠，每个 section
  渲染为 `<section id={section.key}>`，锚点即 section key。
- 点击左栏时 `SettingsNav.goToLeaf()` 不走路由：同路径下用
  `history.replaceState` 改 hash 再滚动（`SettingsNav.tsx:57`），所以不重新挂载。
- 滚动位置是「当前在哪一页」的唯一真源：`CategoryScroll` 的 scroll 监听算出
  `activeSection` 存进 `SettingsStore`（`CategoryScroll.tsx:251`），工具栏再用它反查
  该页对应哪个存储文件（`settings-nav.ts:335 storagePathFor`）。
- 重内容延迟挂载：`dynamic()` + `deferSections` + `IntersectionObserver`
  （`rootMargin: 800px`，`CategoryScroll.tsx:288`）。

## 3. 现有实现的真实问题（无论改不改路由都成立）

1. **锚点对齐是靠补丁维持的**。section 各自异步加载，晚到的内容会把已定位的锚点顶走，
   于是 `CategoryScroll.tsx:177-190` 用 `ResizeObserver` + `pendingAnchorRef` 反复回正，
   还必须在用户滚动/触摸/按键时取消（`:186-195`）。真实路由不需要这套机械装置。
2. **滚过一遍就全量挂载且不回收**。19 个 section（顶层 8 + models 8 + chat 3，约
   5300 行组件代码）一旦进入视口 800px 内就挂载，之后常驻。首屏并不贵（只挂 overview），
   代价出现在「用户把设置从头翻到尾」之后：约 30 次 section 级请求
   （`DocumentParsingSettingsSection` 12 处、`NetworkSettingsSection` 6 处、
   `AgentLoopSettingsSection` 5 处…）同时驻留。没访问过的功能本不该付费。
3. **导航元数据已经半死，并且有一处真实错配**：
   - `settings-nav.ts:269` 写 `href: "/settings#document-parsing"`，但 `page.tsx:61`
     渲染出的 id 是 `knowledge`，链接与锚点对不上（目前侥幸没爆，见下条）。
   - `SettingsLeaf.href` 与 `SettingsCategory.href` 被赋值
     （`SettingsNav.tsx:105,111`）但**全仓库没有任何地方读**——渲染一律走
     `settingsAnchorHref(key)`。所以上面那处错配不会真的断链，只是一份会误导人的死数据。
   - `settings-nav.ts:308-312` 的 `/settings#appearance`、`/settings#llm` 等存储键
     永远命中不了（`storagePathFor` 收到的 `activeSection` 是裸 key），真正生效的是
     `:313` 起的裸键版本；`image` / `video` 也是废弃键。
   - `SettingsStore.tsx:351` 导览第 7 步目标 `tour-nav-memory`：memory 类目已随多用户 /
     学习者子系统删除，该步永远找不到元素，8×80ms 重试后静默失效。
4. **浏览器返回/前进不会回到上一个设置区块**。左栏点击是 `replaceState`，不产生历史条目，
   从「模型」退不回去「外观」。
5. **体积预算全压在一条路由上**。`web/scripts/route_budgets.mjs:18` 给 `/settings`
   单独 840KB 预算，任何单个功能变重都会顶到整条路由。

## 4. 改造的外部耦合（容易漏算的成本）

- **后端产出的深链接形态**。`setup_credential` 工具结果的 `settings_path` 目前是
  字符串 `/settings#llm`，前端 `web/lib/setup-signals.ts:49` 只校验
  `startsWith("/settings")` 后直接 `router.push`（`SetupCredentialCard.tsx:48`）。
  该元数据在本仓库**已无产出方**（注释指向的 `kagweb/capabilities/setup/tools.py`
  随工具层一起删除），来源应是 agent-loop 后端。改路由前必须先确认这个字符串由谁生成，
  否则要把 `setup-signals.ts` 做成一次兼容映射。
- **登录回跳依赖 hash**。`web/shared/auth/return-url.ts:79 inheritLoginHash` 专门把登录页
  的 fragment 带回目标路径，hash 形态的设置链接和登录门禁是耦合的。
- **仓库内 7 处硬编码互链**：`ConnectionsEditor.tsx:54-60`（llm / task-models / search /
  tts / stt / imagegen / videogen）、`TaskModelsEditor.tsx:143`、`ServiceConfigEditor.tsx:1511`、
  `VersionBadge.tsx:78`。
- **测试与 ADR 必须同步改**：`settings-continuous-navigation.test.ts`（断言 section 堆叠
  顺序、断言无 `/settings/about`）、`settings-provider-slices.test.ts`、`auth-return-url.test.ts`、
  `setup-signals.test.ts`，以及 ADR-0002。
- **子路径部署**（AGENTS.md 的 Web Rule）：新路由必须走 `Link` / `router.push` 的相对路径，
  不要在代码里拼部署前缀。现有代码已合规，切路由时保持即可。

## 5. 方案对比

### 方案 A：维持单文档，只修 bug

做 §3 的 1/3/4 修复（错配、死字段、死导览步、可选的历史条目）。

- 成本：约半天，风险极低。
- 收益：消除误导性元数据和静默失效的导览步。
- 不解决：深链接本质仍是「靠滚动对齐」，没访问过的功能仍会被付费，返回键语义仍不对。

### 方案 B：每个功能一个真实路由

`/settings/appearance`、`/settings/network`、`/settings/models/llm`、
`/settings/chat/capabilities`… 共 19 条。

- 收益：URL 粒度最细，天然可分片、可权限门控、可独立预算。
- 成本：19 条路由 + 19 条预算项；左栏点击全部变成路由跳转，
  「连续翻看」的阅读体验消失；旧 `#llm` 链接需要 19 条映射。

### 方案 C（推荐）：按功能域切真实路由，域内保留锚点

顶层功能域各一个真实路由，域内叶子仍是锚点：

```
/settings              → overview
/settings/appearance
/settings/network
/settings/models        → #connections #llm #task-models #search #tts #stt #imagegen
/settings/chat          → #capabilities #starters #attachments
/settings/knowledge
/settings/agent-loop
/settings/about
```

关键点：**路由即「哪个域」，「域内哪一片」仍由锚点承担**。理由——models 的 8 个叶子
共享同一份 `model_catalog.json`、同一套 ServiceConfigEditor，本质是一个域内的并列项；
而「外观 / 网络 / 知识库 / 智能体后端 / 关于」之间才是真正独立的功能。

- 收益：
  - 只有访问到的域才挂载组件、才发请求（§3.2 的代价消失）——但见 §8.1，
    这个收益比本方案初稿声称的小得多；
  - 顶层文档不再需要滚动对齐（§8.3 修正：models / chat 域内仍然需要）；
  - 每域可各自设体积预算和错误边界（现状根本没有 error boundary，见 §8.4）；
  - **provider 结构不用动**：状态本来就挂在 layout 上，切路由不会重复拉
    `/api/settings`、`/api/settings/draft`、`/api/system/status`——
    这正是当前架构让「拆路由」变便宜的地方；
  - **跨路由导览本来就有现成机制**：`SettingsTourOverlay` 已按 `guideStep.route`
    等 pathname 再找 `data-tour` 元素，现在 7 步全写死 `route: "/settings"`
    反而是退化形态；
  - 返回键语义自然正确（§3.4）。
- 成本：8 条路由（不是 19 条）、8 条预算项、旧 `#llm` 类链接需要一次映射
  （8 条，或按 §6 批 3 的一次性重定向统一处理）；**权限门控从集中过滤变成
  每路由各自负责（§8.2，本方案初稿漏算的真实回归风险）**。

## 6. 迁移步骤（分批，每批可独立验证）

- **批 0（与方案无关，先做）**：修 §3.3 的三处——`document-parsing` / `knowledge` 错配、
  删掉无人读的 `href` 字段、更新死掉的 `tour-nav-memory`。同步更新
  `settings-continuous-navigation.test.ts` 中 `href: "/settings#agent-loop"` 这条断言。
- **批 1**：把 `CategoryScroll` 从「滚动是唯一真源」改为「路由优先、锚点兜底」；
  `SettingsNav` 同路径判定改为按域路由判定；删除滚动过程中写 hash 的 `replaceState`
  （`CategoryScroll.tsx:233,253`）。
- **批 2**：新增 8 条域路由，`page.tsx` 拆成薄壳；`settingsAnchorHref(key)` 升级为
  `settingsHref(key)`（域路由返回 `/settings/{domain}`，叶子返回 `/settings/{domain}#{leaf}`）；
  `storagePathFor` 改按 key 直查并删掉 §3.3 的死键。
- **批 3（需用户拍板，见 §7）**：旧链接处理。ADR-0002 的立场是「不保留兼容别名」，
  因此可以接受旧 `#llm` 失效；若接受不了，就在 settings layout 挂一个**一次性**
  hash→路由映射（不是永久重定向）。无论选哪个，这次变更都应记一条新 ADR 覆盖 ADR-0002
  里关于 settings 的那句。
- **批 4**：更新测试（`settings-provider-slices` / `auth-return-url` / `setup-signals`）、
  `route_budgets.mjs` 加各域预算、导览恢复真正的跨路由形态。

## 7. 需要拍板的讨论点

1. **旧 `#xxx` 深链接**：接受失效（符合 ADR-0002），还是加一次性 hash→路由映射？
2. **域内粒度**：models 的 8 个服务保留锚点滚动，还是也拆成 `/settings/models/{service}`？
   （倾向保留锚点：同一份 catalog、同一套编辑器。）
3. **导览**：是否顺带从「7 步同一路由」恢复为跨路由导览？
4. **外部 `settings_path`**：确认由哪个后端生成，是改后端还是前端做映射？

---

## 8. 方案评审（2026-09-15，对 §3–§7 的自我复核）

复核方式：逐条回源码与构建配置验证本方案里下过的断言。**发现 1 处关键数字夸大、
1 处推荐理由不成立、1 处漏算的真实回归风险、1 处「收益」现状并不存在。**
结论是推荐方向不变，但**推荐理由要换**。

### 8.1 【修正】§3.2 「约 30 次请求」是夸大的，性能不是本方案的主要卖点

初稿把 `apiUrl(` / `apiFetch(` 的**全部出现处**（含点击处理器里的「测试连接」
「安装模型」等）当成 mount 时的请求数，得出「约 30 次」。实测只统计
`useEffect` 体内的调用点：

| section | mount 时调用点 | 全部调用点 |
| --- | --- | --- |
| DocumentParsingSettingsSection | 2 | 14 |
| NetworkSettingsSection | 4 | 8 |
| AgentLoopSettingsSection | 2 | 8 |
| StartersSettingsSection | 2 | 4 |
| AttachmentsSettingsSection | 2 | 4 |
| CapabilitiesSettingsSection | 0 | 4 |
| AppearanceSettingsSection | 0 | 0 |
| AboutSettingsSection | 0 | 0 |
| **合计** | **12** | **42** |

真实 mount 成本是 **12 个调用点**（其中数条还是同一端点的重复请求，见 §8.6），
不是 30 次请求。而且 `/settings` 的 840KB 预算**当前是通过的**
（`web/scripts/route_budgets.mjs:18`），仓库里**没有任何实测的性能问题**。
「没访问过的功能不该付费」在事实层面成立，但幅度不足以单独支撑 8 条路由的改造成本。

→ **§3.2 不能作为本方案的首要理由。** 首要理由应换成 §8.3 的滚动对齐机械装置和
§3.4 的返回键语义，二者是确定存在的、可验证的缺陷。

### 8.2 【漏算】权限门控会从「集中过滤」退化为「每路由各自负责」

现状 `adminOnly` 门控只有两个执行点：`page.tsx:84`
（`isSettingsCategoryVisible` 决定哪些 section 进 `CategoryScroll`）和
`SettingsNav.tsx:101,119`（决定左栏渲染哪些行）。**页面内容侧的拦截全靠
`page.tsx` 那一个 filter。** 拆成 8 条路由后这个 filter 消失，每个路由都要
自己重新执行一次门控，否则普通用户直接访问 `/settings/agent-loop`
（`adminOnly: true`）就会渲染出智能体后端设置页。后端会拒绝，但 UI 门已经开了。

这是方案 C 引入的**真实回归风险**，初稿完全没提，反而把「可权限门控」列成了收益。
缓解：把门控收敛成一个路由级守卫（例如每域路由统一走一个
`<SettingsDomainGate domain="agent-loop">`），而不是让 8 个 page 各写一遍。

注：`CapabilityGate` **不会**误伤新路由——`capabilityForPath` 只登记了
`/chat` 一个前缀（`web/lib/capability-routes.ts:22`），`/settings/*` 一律返回
`null` 不受门控。这一点已核实，不属于风险。

### 8.3 【修正】「§3.1 的补丁可删」只对顶层文档成立

初稿说方案 C 之后滚动对齐的补丁（`ResizeObserver` + `pendingAnchorRef` +
取消机制）可以删掉。复核后：**models 域有 8 个 section、chat 域有 3 个，
它们仍然用 `CategoryScroll` + `deferSections` 堆叠**，因此这套机械装置在
models / chat 域内**原样保留**。方案 C 只删掉了顶层文档那一份
（19 段 → 8 段 + 3 段）。除非 models 也拆成 8 条路由（总路由数升到 15+），
否则这部分收益是打折的。

这不推翻方案 C——顶层文档是最大、异步内容最多的那份，删掉它的对齐逻辑仍有价值——
但收益描述必须从「补丁可删」改成「顶层那一份可删」。

### 8.4 【修正】「每域可设错误边界」不是现成收益

初稿把「每域可各自设错误边界」列为收益。实测 `web/app` 与 `web/components` 下
**不存在任何 `error.tsx` / `global-error.tsx` / ErrorBoundary**。所以这不是
「拆分后自然获得的好处」，而是「拆分后必须补上的工作」——尤其因为
`DocumentParsingSettingsSection`（1403 行）、`AgentLoopSettingsSection`（1210 行）
这类重组件各自独立发请求，拆成独立路由后它们崩掉会整页白屏。

### 8.5 【漏算】8 个常驻可见 Link 的预取会侵蚀「按需加载」

`SettingsNav` 用的是 `next/link` 且**未设 `prefetch`**。方案 C 之后左栏是
8 条指向真实路由的常驻可见 Link，Next 在生产模式下会对进入视口的 Link 预取其
RSC payload。这可能把 §8.1 那点「按需加载」的好处吃掉一部分，具体还剩多少
**必须实测**（或对非当前域显式 `prefetch={false}`）。初稿未提此风险。

### 8.6 【新发现】section 级取数纪律已经松动

`NetworkSettingsSection.tsx` 在 mount 时请求 `/api/settings`
（拿 `ui.chat_response_timeout`），而共享 store 在 layout 层**已经拉过同一个
`/api/settings`**（`SettingsStore.tsx:802`）。同一端点每挂载一次 Network 就重复取一次。
这与路由方案无关，但说明「section 各自取数」的边界已经模糊；拆路由时若照抄这个
模式，会把重复请求按域固化。建议并入批 0 一并清理。

### 8.7 评审后的结论

**推荐方向不变，理由重排**：方案 C 由「性能」改为「URL 语义 + 顶层可删滚动对齐 +
返回键正确」；同时承认它带来 §8.2 的门控回归风险，需要配套守卫。

**方案 A 的性价比被初稿低估了。** §3.1/§3.3/§3.4 这三条（锚点对齐补丁、死元数据、
返回键）方案 A 都能修，且**不引入 §8.2 的回归风险**。考虑到 §8.1 已证明性能收益
不成立、§8.3 证明对齐补丁只能部分删，A 与 C 的差距比初稿呈现的小得多。

**因此建议**：先只做批 0（§3.3 的死元数据与错配修复，零风险、立即消除误导），
拿到实际使用反馈后再决定是否推进 C。若推进，批 2 必须同时落地 §8.2 的路由级
门控守卫，否则是「拆了路由、开了权限口子」。

**新增待拍板项**：用户原话「针对每个不同的功能，采用独立的页面」，这里的
「功能」指 8 个**功能域**还是 19 个**具体设置项**？§5 三种方案的取舍完全取决于此。

---

## 9. 实施记录（2026-09-15）

采用**方案 C**（按功能域切真实路由，域内保留锚点），分批全部落地。

### 9.1 最终路由形态

```
/settings              → 概览（索引页，不再堆叠各类目）
/settings/appearance
/settings/network
/settings/agent-loop
/settings/models        → #connections #llm #task-models #search #tts #stt #imagegen
/settings/knowledge
/settings/chat          → #capabilities #starters #attachments
/settings/about
```

### 9.2 各批次实际改动

- **批 0/1 — 单一真源**：`settings-nav.ts` 重写。删掉 `SettingsLeaf.href` /
  `SettingsCategory.href`（无人读、且已漂移），改为 `settingsHref(key)` 从 key 推导；
  新增 `SETTINGS_ROUTES` 与 `settingsDomainForRoute`；`storagePathFor` 改为**按 section key
  直查**（不再按 URL），并删掉永不命中的 `/settings#xxx` 键。
- **批 2 — 路由**：新增 7 个类目路由页；`page.tsx` 瘦身为概览；新增
  `SettingsDomainGate` 承担原先由 `page.tsx` filter 承担的可见性检查。
- **批 3 — 旧链接**：新增 `SettingsLegacyAnchorRedirect`（挂在 settings layout），
  把 `/settings#llm` 这类片段映射到 `/settings/models#llm`。实测 `/settings#network`
  → `/settings/network`、`/settings#llm` → `/settings/models#llm` 均生效。
- **批 4 — 测试/预算/边界**：测试改写为路由契约测试；`route_budgets.mjs` 按路由设预算；
  新增 `settings/error.tsx`（§8.4 指出这是「必须补的工作」而非现成收益）。

### 9.3 实测结果

- `npm run check` 全绿：typecheck / 687 node tests / vitest / lint（0 error）/
  i18n parity+audit / depcruise（358 模块）/ build / perf。
- 路由体积（各页预算内）：`/settings` 274KB，三个类目页各 249KB。
- 浏览器实测：8 条路由各自渲染且高亮正确；域内锚点（`/settings/chat#starters`）
  URL+滚动+双高亮正确；**返回键**从 `/settings/knowledge` 正确回到 `/settings`
  （§3.4 的缺陷已消除）。

### 9.4 实施中发现、方案未预见的三件事

1. **导览在第三步就死掉（既有 bug，被本次实测暴露）**。`TOUR_STEPS` 第 4 步指向
   `tour-nav-models`，而该行在 CLI 后端下被 `llmOnly` 隐藏，于是 `SettingsTourOverlay`
   8×80ms 重试后**什么都不渲染，也没有下一步按钮** —— 用户看到引导凭空消失。
   已修：解析不到目标的步骤由 overlay 自动跳过。实测已从 step 3 直接跳到 step 5。
   （顺带确认第 7 步 `tour-nav-memory` 是同类死步，随 memory 子系统删除而来，已移除。）
2. **`models` 页的拦截文案一度说错了原因**。初版把「后端不适用」和「管理员独占」
   合成一句话，会把人引向一个单用户安装里根本不存在的管理员。已拆成
   `settingsDomainBlockReason` 的两个理由、两套文案。
3. **概览页与 Models 门控自相矛盾（未处置，需决策）**。本机 CLI 后端下
   `/api/settings/agent-loop` 报 `llm_settings_enabled: false`（Models 门控生效），
   但 `/api/settings` 仍返回 `catalog`，于是概览页显示「已配置 0/5 个模型服务」
   并列出 5 个模型服务链接 —— 点进去却是「不显示」提示。同时后端启动日志
   （`kagweb/services/llm/config.py:202`）明确指引用户去 **Settings > Models** 配置模型。
   即：后端让用户去配、前端把该页藏起来、概览页还在展示它。这三者必须统一，
   但属于产品决策（该页到底该不该对 CLI 后端开放），**已保留原状未改**，见下文待决策。

### 9.5 落地后仍待决策

- §9.4.3 的 Models 门控矛盾：是让概览页在门控生效时不再列模型服务，还是本就该对
  CLI 后端开放 Models 页（后端自己的报错信息倾向于后者）。

---

## 10. 代码评审与修复（2026-09-15，实施后评审）

评审发现 1 个实施引入的回归、2 个治理遗漏，已全部修复；残留限制记录如下。

### 10.1 已修复

- **videogen 链接回归（P1）**。`ConnectionsEditor` 的 videogen 服务芯片经
  `settingsHref("videogen")` 解析；videogen 不在 `MODEL_CHILDREN` 里（上游遗留的
  导航遗漏——section 一直在渲染，导航却从不列出），旧方案靠手写字面量
  `/settings#videogen` 掩盖，改造后该 key 解析兜底成了 `/settings` 首页。
  修复：补上 videogen 叶子（`service: "videogen"`，与 `VideoSettingsSection` 对齐），
  导航行、芯片链接、概览服务列表三者同时恢复。并新增契约测试
  「域内渲染的每个 section 必须能被 `settingsHref` 解析为路由叶子」，防止同类漂移。
- **ADR 缺失（P2）**。新增 `docs/adr/0004-settings-routed-per-category.md`，
  修订 ADR-0002 中 settings 的片段地址条款；ADR-0002 状态节已加「Amended by」标注。
- **设计文档状态行（P2）**。文档头仍写「讨论中，未实施」，与 §9 实施记录矛盾。已改。

### 10.2 已知限制（接受，记录在案）

- **域内叶子点击不产生历史条目**。`goToLeaf` 对同路由叶子仍用 `replaceState`：
  返回键能跨域退回（§3.4 修好的部分），但在 Models 内部从 `#llm` 退不回
  `#connections`。原因：域内滚动追踪本身也在持续 `replaceState` 推进 hash，
  点击再入栈会与滚动互相插队，历史里堆满中间锚点。跨域是用户真实会退的粒度，
  域内不是；接受此残留。
- **同路径 legacy 链接依赖 hashchange 监听**。已在 `/settings` 页内以
  `location.hash` 赋值方式实测该路径生效（见 §10.3）。
- **导览最后一步目标缺失时**，overlay 跳步逻辑会把索引推到结束，用户看到的是
  「导览直接结束」而非空步。与中间步跳过体验略不同，可接受。

### 10.3 评审后补测

- 同路径 hashchange 路径：在 `/settings` 上以 `location.hash = "#network"` 触发，
  确认被重定向到 `/settings/network`（此前只实测过跨路径 goto）。
- videogen：`settingsHref("videogen")` 返回 `/settings/models#videogen`
  （契约测试固化）；`VideoSettingsSection` 的 i18n 键在 zh 中齐备。**浏览器级确认
  在本部署上不可得**：本机 CLI 后端使 Models 整域被 `llmOnly` 门控，页面不渲染。
  导航行与 section 的渲染路径与其余 7 个已浏览器验证过的叶子完全同一
  （`visibleSettingsChildren` 的 key 集合过滤），待任一 LLM 门控开放的部署自然验证。

---

## 11. Models 门控矛盾收敛（2026-09-15，用户选定：按叶子拆门控）

### 11.1 裁决依据（实施前实测的数据流）

- KAGWeb 自身调用（会话标题 `title_service.py:133,254`、轮次洞察、历史摘要）由后端
  进程内解析 **task** 服务（`provider_runtime.py:597`，`task_llm_scope`），与 agent
  后端家族无关——CLI 子进程的登录态帮不了进程内 HTTP 调用。
- TTS/STT 走 KAGWeb 自有 `/api/voice/*`（前端 `ChatMessageList.tsx:526` 直调），读
  **tts/stt** 服务，同样与后端无关。
- 真正随后端家族变化的只有**对话模型**（llm 服务/连接凭据）：HTTP 族需要 KAGWeb 带凭据，
  CLI 族自带。
- 结论：`llm_settings_apply`（`builtin.py:189`）的意图（别显示不影响对话的模型配置）
  只覆盖 llm 叶子；类目级隐藏把 task/tts/stt 一并连坐，使启动警告
  （`llm/config.py:196`）在 CLI 后端 + 未配模型的触发场景下**永远无法被照办**。

### 11.2 落地改动

- `llmOnly` 从 `SettingsCategory` 下沉到 `SettingsLeaf`（仅 llm 叶子携带）；
  Models 类目在任何后端下可见，可见性随叶子过滤（导航/section/概览同源）。
- `settingsDomainBlockReason` 收敛为仅 admin-only；`SettingsDomainGate` 相应简化。
- 概览服务列表按叶子可见性过滤，并补上 **任务模型** 的就绪行（`service: "task"`）；
  草稿「去处理」链接在 llm 叶子隐藏时兜底到 Models 路由顶部。
- `TaskModelsEditor` 未配置提示里的「LLM ↗」链接随叶子隐藏（否则指向渲染不出的锚点）。
- 后端 `llm_settings_apply` 语义不变（对话 LLM 是否适用），docstring 更新并注明叶子
  级作用域与理由；前端 `enableLlmSettings` 注释同步。
- 顺带清理：`settings-access.ts` 死变量 `ordinaryAuthenticatedUser`（评审遗留项）。

### 11.3 实测（CLI 后端，llm_settings_enabled=false）

- `/settings/models` 可见：渲染 connections / task-models / search / tts / stt /
  imagegen / videogen 七个叶子；导航无 LLM 行，页面无「不显示」拦截。
- 概览「已配置 0/6」的构成为 任务模型+搜索+TTS+STT+文生图+文生视频——每一项都可点达。
- 启动警告的「Settings > Models」指路恢复有效（任务模型就在该页首个区）。
- llm 配置项未丢失：HTTP 后端部署上照常显示（`llm_settings_enabled=true` 路径不变）。
