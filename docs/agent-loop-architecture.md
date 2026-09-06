# Agent Loop 架构与溯源分析

> 分析日期:2026-09-06 · 分支:`simon` · 方法:源码精读 + git 考古
> (`git log --follow` / `git show` / `-S` 逐提交取证,文中所有结论均附 commit 或 `file:line` 证据;行数与删除量断言以 `git show --numstat` / `wc -l` 全量复核)

本仓库实际存在**两套 Agent Loop**:

1. **产品主循环** — chat 的原生 tool-calling 循环(`kagweb/agents/chat/agent_loop.py`),默认能力,所有对话回合都跑在它上面(solve / mastery / reading 以扩展形式挂载,见 §4);
2. **通用标签驱动引擎** — `kagweb/runtime/agentic/loop.py` + `labeled_step.py`,服务 deep_research、deep_question、PageIndex RAG 推理等内部流水线。

两者的关系只有通过溯源才能看清:主循环曾长期跑在第二套引擎的标签协议上,2026-06-11 起切换为原生 tool calling,引擎则退守到协议可控的内部场景。

---

## 1. 主循环:chat 的原生 tool-calling 循环

回合如何到达这里:`ChatOrchestrator`(`kagweb/runtime/orchestrator.py`)经 capability routing 选中能力后调用其 `run()`——本文从能力层开始,上游路由不在范围内。

入口链:

```
ChatCapability (agents/chat/capability.py)
  → AgenticChatPipeline (agents/chat/agentic_pipeline.py, ~1900 行宿主层)
    → AgentLoop.run() (agents/chat/agent_loop.py, ~1200 行)
```

注:manifest 里的 `stages=["exploring", "responding"]` 是历史遗留——现仅被 `runtime/registry/capability_registry.py:157` 暴露进能力目录条目(展示用途),不参与循环实现;现行实现是单一 stage(`agent_loop.py:69` 的 `LOOP_STAGE = "responding"`)。

核心契约(`agent_loop.py` 文件头):**一个回合 = 一条持续增长的消息列表上跑的单一循环;每一轮 = 一次 LLM 流式调用**。

### 1.1 每轮分流(`AgentLoop._run_loop`,`agent_loop.py:331-580`)

- **有 `tool_calls`** → 本轮文本默认是"叙述"(narration,工具调用的开场白),实时流给用户;助手消息(文本 + tool_calls)入列,工具并行派发,结果以 `role=tool` 回填,继续下一轮。需要把工具前文本当答案展示的模式可将其标记为 answer-visible。
- **无 `tool_calls`** → 本轮即 finish:文本就是最终答案(已经流式播完),循环结束。首轮就无工具调用是"无需探索"的快路径。

### 1.2 预算与三段保险

- 探索轮预算:`AgenticChatPipeline.effective_max_rounds()`;
- 预算耗尽 → **结算期**(settlement,`MAX_SETTLEMENT_ROUNDS = 3`),工具仍可用,服务于已发起的 `ask_user` 等交互闭环;
- 结算耗尽 → `_forced_finish` 强制一次无工具收尾。总上限 = 探索 + 4(`agent_loop.py:67-74`)。

异常与异常态恢复:

- **中途 LLM 失败**:第 N 轮失败时,若前面已收集到有用工作则强制收尾打捞(`reason="error"`);首轮失败或流已部分输出(`LLMProviderTransportError.partial_response`)才向上抛——重放会复述已见文字(`agent_loop.py:357-378`)。
- **截断**(`finish_reason ∈ {length, max_tokens, …}`):保留可见前缀,注入"从截断处继续"指令重试。
- **空 finish**(整轮只有 `<think>` 推理):保留原稿,nudge 一次("要么执行计划要么直接写答案"),只 nudge 一次。
- **capability finish guard**:能力(如 mastery 的测验卡)可拒绝一次不合格的纯文本收尾并重定向;只重定向一次,二次拒绝则提示用户重试。

### 1.3 流式与协议兜底(层层设防)

| 组件 | 位置 | 作用 |
|---|---|---|
| `InlineThinkFilter` | `agent_loop.py:98-150` | 流上增量剥离 `<think>`/`<thinking>` 标签(部分 provider 把推理塞进 content 通道);原始文本原样回灌对话 |
| `ToolCallAccumulator` | `runtime/agentic/tool_call_stream.py` | 聚合 `delta.tool_calls` 增量 |
| DSML 兜底解析 | `agents/chat/dsml_tool_calls.py` | 不支持原生 FC 的 DeepSeek 部署会把工具调用以全角特殊标记(`<｜｜DSML｜｜invoke …>`)写进 content;正则解析回结构化调用(issue #666) |
| provider 重试 | `_PROVIDER_RETRY_DELAYS = (0.5, 1.5)` | 只覆盖 SSE 首帧之前;已出输出后重放不安全 |

### 1.4 上下文管理

- 上下文裁剪:chat 侧在 `_call_llm` 发请求前调用 `pipeline._guard_context_window`(`agent_loop.py:774`,实现于 `agentic_pipeline.py:1547`);对比标签引擎在循环每轮开头调 `host.guard_context_window`(`loop.py:220`)。`_fold_context_checkpoint` 在工具轮之后推进压缩检查点(`agent_loop.py:555`);
- 首轮可带 `initial_tool_choice` 引导;`defer_visible_output` 配合 capability finish guard,防止不合格答案先流出去。

### 1.5 `ask_user` 暂停 / 恢复(协议内)

工具请求 pause 时回合保持存活;宿主 `_await_user_reply_and_resolve` 等待用户回复,把答案替换进对应的 `role=tool` 消息后循环原位继续——用户的回答对模型而言就是一次普通工具结果。未恢复(用户弃走)则以未完成态终止,悬而未决的问题即回合的最终产物。

## 2. 第二套:标签驱动的通用引擎

`runtime/agentic/loop.py` 的 `run_agentic_loop` 是能力无关的迭代调度器。

- **协议**:要求模型每轮回复第一行写 `` ``LABEL`` ``(双反引号包裹);`LabelProtocol`(`loop.py:39-64`)声明词表:`allowed / terminal(终止) / intermediate(继续) / final(正文) / tool_label`。`final` 与前两者独立——终止标签可不播正文(REPLAN),中间标签也可播正文(chat 旧 PAUSE)。
- **单步原语**:`labeled_step.py`(630 行)负责一次流式调用:探测前 64 字符内的标签(容忍反引号变体、裸标签、零宽字符,`labels.py:classify_label`);中间标签的正文流入 reasoning 子轨迹,final 标签的正文缓冲待协议验证后再放行;`<think>` 前奏检测、usage trailer 1s 宽限、8s 空闲兜底。
- **违规修复**(`loop.py:_protocol_violation`):缺标签 / 正文夹带第二标签 / 带 TOOL 标签却无调用 / 非工具标签带调用,分类为 violation key → 播警告 + 模型原稿(截 500 字符)+ 修复指令回注重试;另有未知标签的 `unknown_action` 防御兜底(`loop.py:340-355`)。
- **宿主回调** `LoopHost`(Protocol):guard_context_window、build_iteration_trace_meta、dispatch_tools、resolve_pause、emit_final、validate_terminal、force_finalize、可选 before_iteration / on_intermediate——循环核保持通用。
- **现行使用者**:research pipeline(THINK/TOOL/APPEND/FINISH,APPEND 经 `on_intermediate` 变更动态话题队列)、question pipeline、PageIndex RAG 推理(`services/rag/pipelines/pageindex/reasoning.py`)。

## 3. 两套循环的黏合点:LoopExtension

solve / mastery / reading 不是独立循环,而是以 `LoopExtension` 协议(`capabilities/protocol.py:38`)挂进 chat 循环:

- 注入 system prompt 块(`PromptBlock`)与 pre-loop seed / briefing;
- 提供 owned tools 与每轮 kwargs 增强(solve 的 plan/replan 会话即此接入,`capabilities/solve/loop.py`);
- 无工具轮的 finish guard / final-text override / 工具轮输出策略。

即 AGENTS.md 所述 "guided learning = chat 循环 + mastery 工具" 的实现处。

> 注意:AGENTS.md 的能力表(`chat / deep_solve / deep_research / ...`)与现行目录结构(`capabilities/solve/`、`agents/chat/`)已脱节——§4 的演化史解释了原因,以代码为准。

---

## 4. 溯源:演化时间线

| 时间 | 提交 | 形态变化 |
|---|---|---|
| 2025-12-28 | 初始提交 | DeepTutor,`src/` 布局;`guide/agents/chat_agent.py` 等专职小 agent(BaseGuideAgent 单发 LLM 调用),没有统一循环 |
| 2026-03-20 | `c15e22a4` | 引入 tutorbot:IM 助手式独立 `AgentLoop`(`tutorbot/agent/loop.py`,795 行:MessageBus / 频道 / cron / spawn 子代理 / team 协作,max_iterations=40)——OpenClaw/nanobot 一系个人 AI 助手架构,Partners 的前身 |
| 2026-05-13 | `15968072` | chat 重构为标签协议单循环(FINISH/TOOL/THINK/PAUSE);exploring→responding 两阶段成为历史 |
| 2026-05-21 | `23ca3024` | 抽出共享引擎 `core/agentic/`(loop 458 行 + labeled_step 630 行 + tool_dispatch 517 行);research/solve/question 重建其上,一次提交删除约 6,500 行 agent 实现代码(decompose/manager/note/rephrase/reporting、planner/solver/writer、main_solver、idea_agent/generator;连 prompts/tests 合计约 1.39 万行) |
| 2026-05-27 | `b2f2002a` | 推理模型经 OpenAI 兼容端点启用原生 tool calling(#528)——离开标签协议的前奏 |
| 2026-06-11 | `46093e5e` | **分水岭**:chat 弃标签协议,新建 `agents/chat/agent_loop.py`(663 行初版)走原生 tool calling;同提交删除整个 `tutorbot/agent/` 子系统(实测 5,913 行);README 重写并新增 `assets/figs/system/chat-agent-loop.png` |
| 2026-07-24 | `baa49d68` | DeepSeek DSML 文本工具调用解析(issue #666) |
| 2026-09-01 | `02ba5679` | 引擎 `core/agentic` → `runtime/agentic`(架构分层:从 core 提升为 runtime) |
| 2026-09-03 | `5b1eee2d` | 全库 DeepTutor → KAGWeb 改名 |

### 三条演化逻辑

1. **多 agent 编排 → 单循环 + 确定性骨架。** 2026-05-21 重构把每个能力的多角色 agent 团队替换为"一个循环 + 外部确定性代码管状态"(solve 的 plan/replan 会话、research 的动态话题队列是延续)。多智能体性从"多个 LLM 循环互相调用"退化为"一个循环 + 协议化工具"。
2. **文本协议 → 原生 function calling。** 标签协议本质是 ReAct 时代的文本状态机;`b2f2002a` 先给推理模型开原生通道,`46093e5e` 让 chat 彻底切换——"模型宣布做完即结束,不再向模型索要标签",与业界从 ReAct 迁向 provider 原生工具调用的节奏同步。
3. **引擎未被丢弃,而是退守协议可控场景。** `runtime/agentic/labels.py` 的模块文档串明确记录了这段历史(含 commit 号 `46093e5e`):标签引擎继续服务 research / question / PageIndex 这类**内部流水线**——协议由项目自己的提示词控制,结构收益大于成本;面向用户的对话循环则完全交给原生工具调用。

> 注脚:分水岭提交 `46093e5e` 的 message 只有 "update msg" 四个字——这次最重要的架构迁移只能从 diffstat(663 行新循环 + 约 5,900 行删除 + README 重写)而非提交信息里读出。

---

## 5. 关键文件索引

| 文件 | 角色 |
|---|---|
| `kagweb/agents/chat/agent_loop.py` | chat 主循环(轮调度、预算、打捞、nudge、续写) |
| `kagweb/agents/chat/agentic_pipeline.py` | 宿主:工具组合、KB seed、briefings、派发、暂停、finish guard |
| `kagweb/agents/chat/dsml_tool_calls.py` | DeepSeek 文本工具调用兜底解析 |
| `kagweb/runtime/agentic/loop.py` | 标签驱动通用循环调度器 |
| `kagweb/runtime/agentic/labeled_step.py` | 单次流式调用 + 标签协议路由 |
| `kagweb/runtime/agentic/labels.py` | 标签解析与违规检测(文档串含演化史) |
| `kagweb/runtime/agentic/tool_dispatch.py` | 并行工具派发、pause/terminate 聚合 |
| `kagweb/capabilities/protocol.py` | `LoopExtension` / `PromptBlock` 挂载协议 |
| `kagweb/capabilities/solve/loop.py` | LoopExtension 挂载示例(solve 骨架) |
| `kagweb/agents/research/pipeline.py`、`agents/question/pipeline.py`、`services/rag/pipelines/pageindex/reasoning.py` | 标签引擎现行使用者 |

测试:`tests/core/agentic/`(引擎单测:tool_arg_guard、tool_call_stream、dispatch 事件与暂停时序等)、`tests/core/test_agentic_loop_intermediate.py`、`tests/core/test_labeled_step_*.py`。
