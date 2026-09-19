# Agent Loop 历史与会话续接改进方案(intellect / codex / claude code / opencode / zcode 横评)

日期:2026-09-19
状态:**M1+M2 已实施**(提交见 git log,2026-09-19);M3/M4 待做(§8 修订记录见文末)

## 0. 命题校准

「ACP 对历史对话处理支持不足,不是连接 Agent Loop 的最佳方案」——这个论断
**对一半**:

- **对的部分**:KAGWeb 当前把「agent 自持历史」当作 ACP 的偶然特性而非
  一等能力。后果是真正的主力部署形态(one-shot CLI 预设)每回合把**被预算
  截断的内联历史**塞进 prompt,旧细节退化成摘要;而各家 CLI 其实都有原生
  会话续接机制,一个都没用上。同时 ACP 自身有一个真实缺口:agent 侧会话
  丢失时**静默开新会话**,KAGWeb 明明持有完整转录却不注入(§2 缺口 G-1)。
- **需要修正的部分**:ACP 的"agent 自持会话"恰恰是五个可选连接形态里
  **最正确的历史模型**——问题不是 ACP 选错了,而是它 solo。改进方向不是
  换掉 ACP,而是**把它的强项推广到全部族,并给它补上 KAGWeb 侧兜底**。

## 1. 五家 agent loop 的历史机制横评

| Agent | 连接形态 | 原生会话续接 | 历史存放 | 证据 |
| --- | --- | --- | --- | --- |
| intellect-agent | ACP(`intellect acp`)/ HTTP runs | ✅ ACP `load_session`;会话落自家 state.db(含 model/cwd) | agent 侧持久 | KAGWeb `acp_session_store` 记录 (config_key, session_id, cwd) 供重挂;HTTP runs 收 `conversation_history` |
| claude code | one-shot CLI(`-p` stream-json) | ✅ `--session-id <uuid>`(首回合指定)/ `-p --resume <id>`(续)/ `--fork-session`(分支派生) | agent 侧 `~/.claude/projects/*.jsonl` | 本机 `claude --help` 实测 |
| codex | one-shot CLI(`exec --json`) | ✅ `exec resume <SESSION_ID> [PROMPT]`(UUID 或 thread 名);JSON 流 `thread.started` 携带 `thread_id` **可捕获** | agent 侧 `~/.codex/sessions` | 本机 `codex exec resume --help` 实测 + 探针确认 thread.started |
| opencode | one-shot CLI(`run --json`) | 文档口径有 `--continue` / `--session <id>`;另有 `opencode serve` HTTP 模式(session REST + SSE) | agent 侧 | **待实测**(本机未装);serve 模式若证实,更适合走 KAGWeb 的 HTTP 族而非 one-shot |
| zcode | **无 CLI 面**(桌面 harness,无可验证的 print/resume 模式) | 平台内有持久会话历史(跨会话可引用),但无法从 KAGWeb 驱动 | agent 侧 | 本机核实无 `zcode` 二进制;出现 `-p/--resume` 类接口时可按 claude-code 同款接入 |

**结论**:除 zcode 外,四家的「agent 自持历史 + 按需续接」能力全部存在且
可从外部驱动;KAGWeb 却只在 ACP 族使用了它。

## 2. KAGWeb 现状与真实缺口

现状:`cli_backend` 每回合起全新子进程,`_prompt_with_history` 内联折叠
被预算截断的转录;`http_backend` 原样传结构化 history;只有 `acp_backend`
有会话续接(经 `acp_session_store`)。

- **G-1(ACP)**:`_spawn_session` 里 `load_session` 失败 → 静默
  `new_session`——agent 记忆全失而 KAGWeb 转录俱在,不注入任何兜底。
- **G-2(CLI)**:内联历史是截断+摘要的有损通道;原生 resume 完全未用。
- **G-3(跨后端)**:操作员切换 primary(ACP→claude-code 或反向)后,
  KAGWeb 历史俱在但每个 agent 冷启动。
- **G-4(通用)**:KAGWeb 是历史的持久真相源,却没有一个"任何 agent 都能
  读到完整转录"的通道(即先前挂起的 history 文件化)。

## 3. 改进方案:三层历史策略

**L0 通用转录文件(兜底层,吸收 pending-decisions 决策二)**:每回合把
本会话转录(含被引用的历史会话)写入会话工作区(既有
`materialize_attachments` 机制),prompt/manifest 渲染 path 行——manifest
header 的 "read it when the preview is not enough" 指引现成。任何族、任何
冷启动场景都能按需读全量历史。

**L1 原生 resume(主力层,逐族启用)**:通用化 `acp_session_store` 为
`agent_session_store`:(backend config_key, KAGWeb session) → agent
session id。各族续接方式见 §4。resume 生效时**停用内联折叠**,只发新
回合——历史由 agent 自己持有,token 账算给 agent 侧。

**L2 内联兜底(降级层,即现状)**:resume 不可用/失败(如 agent 侧会话
被清)→ 清映射,回退现有内联折叠 + L0 文件。失败必须可观测
(progress 事件),不再静默。

优先级表述:**L0 是底线,L1 是优化,L2 是降级**——三层叠加后,G-1~G-4
全部关闭,且不改变任何族的安全模型(子进程登录态、工作区边界均不动)。

## 4. 逐族落地要点

- **claude-code**:首回合 `-p --session-id <uuid>`(KAGWeb 按 KAGWeb 会话
  铸 UUID 入 store);后续 `-p --resume <uuid>`;KAGWeb 编辑分支
  (branch)→ `--fork-session` 派生新 agent 会话并更新映射(分支语义天然
  对齐)。`{model}` 替换与按回合模型选择不变。
- **codex**:translator 捕获 `thread.started.thread_id` 入 store;后续回合
  `exec resume <id> --json`(stdin/prompt 传参方式与现状一致)。
- **opencode**:先实测 `run --session`;若 serve 模式证实,新增
  `opencode`(HTTP 族)预设走 session REST + SSE——比 one-shot 更贴合,
  与 intellect HTTP 同构。
- **intellect ACP**:保持;补 G-1——load_session 失败时,先确保 L0 转录
  文件已落工作区再发首回合 prompt,并把"会话已重置"作为 progress 事件
  告知用户;可另在首回合 prompt 头注入 KAGWeb 滚动摘要(短,不替代 L0)。
- **intellect HTTP / hermes 等 HTTP 族**:保持现状(结构化 history 即其
  原生机制);受益于 L0 的 path 行。
- **zcode**:不出预设;出现可用 CLI 面后按 claude-code 模式接入(映射 +
  resume + L0)。

## 5. 分阶段实施

| 阶段 | 内容 | 验收判据 |
| --- | --- | --- |
| M1 | `agent_session_store`(泛化 acp_session_store,含迁移)+ claude-code resume/fork + L2 兜底 | 本机真实 claude 多回合:agent 侧续接生效,内联折叠停用;删 agent 会话文件后回退可观测 |
| M2 | codex thread_id 捕获 + `exec resume` + 兜底 | 同上;translator 单测钉 thread.started 映射 |
| M3 | L0 转录文件 + manifest path 行(全族)+ ACP G-1 兜底注入 | E2E:ACP 删会话重挂后 agent 仍能答出转录内容;脚本 `verify_attachment_e2e.py` 同款方式验证 |
| M4(待实测) | opencode --session / serve 预设;zcode 预设 | 安装 opencode 后实测决定形状 |

## 6. 与既有决策的关系

- 吸收 `2026-09-19-pending-decisions-credentials-and-history.md` 决策二:
  L0 就是该决策的实施形态,触发条件由"CLI 长会话痛点"放宽为"本方案 M3",
  因为它同时服务 G-1/G-3。
- 每回合模型选择(`backend_model`/`{model}`)、信任边界(env 白名单、
  工作区 allowlist)、审批/clarify 机制全部不受影响。

## 7. 风险与开放问题

- resume 后 KAGWeb 侧 `context_window` 预算的语义变化:历史在 agent 侧,
  预算只约束新增引用类材料(profile 已有 `context_window` 字段,沿用即可)。
- claude `--session-id` 要求 UUID:与 KAGWeb 会话 id 空间隔离,映射表是
  唯一真相源;删除 KAGWeb 会话时需同步清理映射(挂 `forget_acp_sessions_for`
  同款钩子)。
- codex `exec resume --json` 的 flag 组合与 `thread_id` 稳定性:M2 首项
  实测先行。
- opencode serve 模式的认证/多会话形状:M4 实测后定。

## 8. 实施修订记录(2026-09-19)

1. **M1+M2 已落地**(真机验证:claude 两回合——T1 `--session-id` 铸 id 入库,
   T2 `--resume` 在**内联折叠停用**下答出仅存在于 agent 会话中的暗号;
   codex `exec resume <id> --json` 真机证实以同一 `thread_id` 重挂)。
   `acp_session_store` 未动,新增姊妹模块 `agent_session_store`(纯字符串 id,
   无 config_key 绑定——理由见模块 docstring);会话删除时经
   `workspace_cleanup` 同步清理。
2. **fork(`--fork-session`)延期**:分支身份无法用 `branch_parent_id` 表达
   (顺序回合的 parent 逐回合变化),需要"祖先链分叉检测"才能真正对齐
   KAGWeb 的编辑分支;当前与 ACP 行为保持一致(分支共享 agent 会话),
   后续单独立项。
3. **L2 兜底语义定稿**:仅当 resume 回合在**任何事件产出前**失败且 stderr
   命中会话丢失标记(`_SESSION_MISSING_MARKERS`)时重试一次——中流失败
   不重试(agent 已做功,重跑会双倍执行);重试前发 progress 事件、清映射、
   重铸 session id。
4. M3(L0 转录文件 + ACP G-1 兜底)与 M4(opencode/zcode)保持原方案,
   待后续批次。
5. **第一轮代码评审修复**:降级路径历史丢失——resume 注入被跳过时
   (操作员自带 session flag、codex 命令非 `exec` 形状)原先**同时**停用了
   内联折叠,回合会失去全部上下文;现 `resume_active` 以"flag 真正可注入"
   为准,任何降级都恢复折叠转录。`--continue` 前缀误匹配(如
   `--continue-on-error`)收窄为精确/`=` 形态匹配。claude 真机实测:缺失
   会话的报错文案 "No conversation found with session ID: …" 确认命中
   L2 标记。consult 回合沿用 `::consult::` 派生键,与 ACP 语义一致。
