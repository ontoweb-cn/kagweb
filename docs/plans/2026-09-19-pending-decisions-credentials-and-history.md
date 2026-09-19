# 待决策记录:凭据打通定位 与 CLI 长会话 history 文件化时机

日期:2026-09-19
状态:**两项均待用户拍板**;本文是决策材料,不是实施方案
来源:backlog 盘点(同日);凭据打通的事实底账见
`docs/backend-llm-deployment.md` §1.5(凭据双份互不相通)与 §六 P3 行

---

## 决策一:`codex_auth` / in-process LLM 层的长期定位

### 1.1 现状事实(2026-09-19 代码核实)

- `kagweb/services/codex_auth/` 是一套完整的 Codex OAuth 编排:回环回调登录、
  `CodexCredentialStore` 凭据存储、账号级 live 模型目录(`catalog.py`)。
- 两个产出:`openai_codex` provider(供 **in-process LLM 层**)与
  `reconcile_codex_catalog_update`(settings 保存时并入模型目录)。
- **in-process LLM 层今天只剩辅助调用**:会话标题/洞察/摘要(task 服务,已有
  独立 task 档可指向任意便宜模型)、doctor 诊断。对话本体每回合都走 agent loop,
  不经过它。
- `codex` CLI 子进程读 `~/.codex/auth.json`,**没有 env 注入口**;KAGWeb 的
  codex_auth 凭据存自家 store,与 `~/.codex` 互不相通(§1.5 有测试钉死这一独立)。
- 对照:**claude-code 预设今天就能由操作员打通**——profile env 填
  `ANTHROPIC_AUTH_TOKEN` / `ANTHROPIC_BASE_URL` 即可(env 是子进程唯一凭据通道,
  机制现成)。codex 特殊在 auth.json 不可 env 注入。
- 本机活例证:codex 子进程用自身 `config.toml` 的 GLM 网关;"操作员自己管理
  CLI 登录/网关"正是 CLI 族的设计前提。

### 1.2 三个选项

| 选项 | 内容 | 后果 |
| --- | --- | --- |
| **A. 退役** | task 服务配普通 provider 档;codex_auth + openai_codex provider 冻结/移除 | 成本最低;codex 预设凭据打通随之**取消**;需同步清理 `reconcile_codex_catalog_update` 牵动的 catalog/settings 逻辑(中等规模,单独立项) |
| **B. 维持现状** | 保留 codex_auth 只为辅助调用供血,不做 CLI 打通 | P3 关闭为"不做",零改动;代价是维护一整套 OAuth 编排只喂标题生成 |
| **C. 全力打通** | 以 codex_auth 为凭据源,给 codex 预设做 `CODEX_HOME` 托管目录(KAGWeb 写 auth.json/config.toml) | 收益场景窄("部署机有 KAGWeb OAuth 但无本地 codex login");代价:凭据落盘(at-rest)、`CODEX_HOME` 整体接管子进程配置、上游升级可能改 auth.json 形态(脆弱)、token 刷新职责需另设计 |

### 1.3 建议

**A 或 B**。理由:C 的收益面狭窄,而脆弱性(上游私有文件格式)+ 安全代价
(凭据落盘)不成比例;本机实践已证明"操作员自管 CLI 登录/网关"可用。
选 C 前必须先验证:auth.json 精确形态(试验)、`CODEX_HOME` 重定向后子进程
的完整配置需求、刷新职责归属。

---

## 决策二:one-shot CLI 长会话 history 文件化的时机

### 2.1 现状事实(2026-09-19 代码核实,三族语义不同)

| 族 | history 的去向 | 文件化相关性 |
| --- | --- | --- |
| ACP(当前部署) | **不消费 `request.history`**——`load_session` 重挂,agent 自持转录 | 无 |
| HTTP | 结构化 `conversation_history` 字段交服务(`http_backend.py:671`) | 无(服务侧事务) |
| one-shot CLI | `_prompt_with_history` 内联折叠进 prompt,ContextBuilder 滚动摘要按预算封顶——长会话不爆,但**旧细节退化为摘要** | 唯一相关族 |

### 2.2 重要修正:机制地基已经存在

backlog 原注"价值待评估";本轮核实后评估**转为乐观**:附件已走通完整管线——
落盘会话工作区(`materialize_attachments`,已按 CLI 族门控)→ manifest 渲染
`path` 行 → header 现成提示 "Rows with a `path` field point at the full file
on disk — read it when the preview is not enough"(`source_inventory.py:184-188`)。
而 history 引用今天只内联 `_load_history_session` 序列化的 `full_text`/preview。
**文件化 = 让 history 转录走附件同款管线**,改动面小(半天级):
`_add_fresh_history` / `_add_historical` 对超过阈值(建议 8k 字符)的转录调
materialize 生成 path 行,其余(族门控、manifest 指引、工作区清理、测试
fixture)全部复用附件的既有机制。

### 2.3 时机判断

- **现在不做的核心理由**:当前部署是 ACP,收益为零;one-shot CLI 尚未出现
  "引用大会话后旧细节丢失"的实际痛点。
- **触发条件(满足其一即做)**:
  1. 出现以 claude-code/codex 为 primary 的真实长会话使用,且用户抱怨被
     摘要掉的细节;
  2. 引用的 history 会话大到 manifest 内联文本明显挤压当回合有效上下文。
- **风险**(届时评估,均已被附件趟平):转录写盘的隐私面(工作区清理机制现成);
  agent 可能忽略文件(manifest header 指引免费复用)。

---

## 状态

| 项 | 状态 | 复评条件 |
| --- | --- | --- |
| 凭据打通定位 | **待拍板**(建议 A/B) | 用户选定后:选 A → 出退役清理实施方案;选 C → 先做 1.3 的三项验证 |
| history 文件化 | **挂起**(触发即做,半天级) | §2.3 任一触发条件成立 |
