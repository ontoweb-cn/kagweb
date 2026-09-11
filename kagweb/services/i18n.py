"""Small runtime i18n helper for backend-facing user messages."""

from __future__ import annotations

from typing import Any


def _parse_language(language: str | None) -> str:
    raw = (language or "en").strip().lower()
    if raw.startswith("zh") or raw in {"cn", "chinese"}:
        return "zh"
    return "en"


_MESSAGES: dict[str, dict[str, str]] = {
    "en": {
        "api.content_required": "content is required",
        "api.invalid_channels_config": "Invalid channels config",
        "api.partner_already_exists": "Partner '{name}' already exists",
        "api.partner_not_found": "Partner not found",
        "api.partner_not_found_or_not_running": "Partner not found or not running",
        "api.partner_not_running": "Partner not running",
        "api.partner_stopped_start_required": "Partner is stopped. Start it before chatting.",
        "api.soul_already_exists": "Soul '{name}' already exists",
        "api.soul_content_empty": "Custom soul content is empty",
        "api.soul_library_not_found": "Soul '{name}' not found in library",
        "api.soul_not_found": "Soul not found",
        "api.tool_not_found": "Tool '{name}' not found",
        "cli_apps.abi_mismatch": (
            "The CLI app {app!r} was installed for {installed} but this runtime is "
            "{current}. An administrator needs to reinstall it."
        ),
        "cli_apps.args_required": (
            "{tool} needs an 'args' array — one command-line argument per element."
        ),
        "cli_apps.entry_admin_only": (
            "CLI apps are installed by an administrator; ask yours to add this one."
        ),
        "cli_apps.install_in_progress": "That app is already being installed.",
        "cli_apps.not_in_catalog": "No CLI app named {id!r} in the catalog.",
        "cli_apps.not_installed": (
            "The CLI app {app!r} is not installed on this deployment any more."
        ),
        "cli_apps.still_running": "{app} is still running ({seconds}s)",
        "mcp.configure_command_or_url": "Server {name!r}: configure either a command (stdio) or a url.",
        "mcp.configure_before_testing": "Configure either a command (stdio) or a url before testing.",
        "mcp.server_error": "Server {name!r}: {error}",
        "mcp.server_missing": "No server named {name!r} in your list.",
        "mcp.not_oauth": "This server does not use OAuth; give it a credential instead.",
        "mcp.oauth_callback_incomplete": "The authorization response was incomplete.",
        "mcp.oauth_callback_unknown": "That authorization has expired or already completed. Start it again.",
        "mcp.oauth_done": "Authorized. You can close this tab.",
        "mcp.oauth_failed": "Authorization failed.",
        "mcp.catalog_entry_missing": "No MCP service named {id!r} in the catalog.",
        "mcp.entry_admin_only": (
            "This service runs as a local command and can only be added by an administrator."
        ),
        "mcp.tool_not_available": (
            "This tool is not available in this conversation. "
            "Only the tools listed in the prompt can be called."
        ),
        "chat.stub_notice": (
            "KAGWeb is running as a framework shell — the conversation backend is not "
            "connected yet. Model providers, sessions, settings and Partners are ready; "
            "conversation comes from the agent-loop integration "
            '("agent_loop" in system.json).'
        ),
        "agent_loop.spawn_failed": ("Agent-loop backend {backend!r} failed to start: {error}"),
        "agent_loop.exited": ("Agent-loop backend {backend!r} exited with code {code}{detail}"),
        "agent_loop.timeout": ("Agent-loop backend {backend!r} timed out after {seconds}s."),
        "agent_loop.http_status": (
            "Agent-loop backend {backend!r} returned HTTP {status}: {detail}"
        ),
        "agent_loop.http_failed": ("Agent-loop backend {backend!r} request failed: {error}"),
        "agent_loop.unknown_backend": (
            'Unknown agent-loop backend {backend!r}; check the "agent_loop" settings.'
        ),
        "agent_loop.command_required": (
            'Agent-loop backend {backend!r} needs a command; set "command" in the '
            '"agent_loop" settings.'
        ),
        "agent_loop.url_required": (
            'Agent-loop backend {backend!r} needs a url; set "url" in the "agent_loop" settings.'
        ),
        "agent_loop.empty_answer": (
            "The agent-loop backend {backend!r} finished without producing an answer."
        ),
        "agent_loop.approval_header": "Approval needed",
        "agent_loop.approval_prompt": ("The agent wants to run {tool!r}. Allow it to continue?"),
        "agent_loop.approval_decision": ("Approval for {tool!r}: {choice}."),
        "agent_loop.approval_choice_once": "Allow once",
        "agent_loop.approval_choice_once_hint": "Approve this single request",
        "agent_loop.approval_choice_session": "Allow for this session",
        "agent_loop.approval_choice_session_hint": "Approve similar requests in this session",
        "agent_loop.approval_choice_always": "Always allow",
        "agent_loop.approval_choice_always_hint": "Remember this approval",
        "agent_loop.approval_choice_deny": "Deny",
        "agent_loop.approval_choice_deny_hint": "Do not run it; tell the agent no",
        "agent_loop.clarify_header": "The agent is asking",
        "agent_loop.clarify_prompt": "The agent needs more information to continue.",
        "agent_loop.clarify_placeholder": "Type your reply…",
        "agent_loop.clarify_decision": "Answer sent to the agent.",
        "agent_loop.clarify_skipped": "No answer was given; the agent will continue without it.",
        "agent_loop.request_unsupported": (
            "The backend requested {kind} for {tool!r} but cannot receive an answer; "
            "continuing without it."
        ),
        "agent_loop.runs_no_run_id": (
            "Agent-loop backend {backend!r} started a run but returned no run_id."
        ),
        "agent_loop.acp_too_many_sessions": (
            "Too many active agent sessions (max {max}); try again after one finishes."
        ),
        "agent_loop.acp_probe_timeout": (
            "Agent-loop backend {backend!r} did not finish the ACP handshake within {seconds}s."
        ),
        "agent_loop.runs_poll_gave_up": (
            "Agent-loop backend {backend!r} lost the event stream and the run status "
            "never reached a terminal state while polling."
        ),
        "agent_loop.acp_sdk_missing": (
            "Backend {backend!r} uses the Agent Client Protocol transport; install "
            "the SDK with `pip install kagweb[acp]`."
        ),
        "agent_loop.acp_init_failed": (
            "Agent-loop backend {backend!r} failed the ACP handshake: {error}"
        ),
        "agent_loop.acp_turn_failed": (
            "Agent-loop backend {backend!r} lost the ACP connection mid-turn: {error}"
        ),
        "agent_loop.consult_manifest": (
            "<agent-consultation>\n"
            "Other agent backends are available for consultation:\n"
            "{agents}\n"
            "If consulting one would materially improve the answer, end your reply "
            "with ONLY this fenced block and nothing after it:\n"
            "```{fence}\n"
            '{{"agent": "<backend id from the list>", "question": "<the question to ask>"}}\n'
            "```\n"
            "You will then receive the consultation result and must answer the user "
            "directly. At most {budget} consultation(s) this turn. When no consultation "
            "is needed, just answer the user.\n"
            "</agent-consultation>"
        ),
        "agent_loop.consult_followup": (
            "The consultation result is attached as the last message. "
            "Produce your final answer for the user now."
        ),
        "agent_loop.consult_unknown_agent": (
            "Consult request referenced unknown agent '{agent}'; answering directly."
        ),
        "agent_loop.consult_empty": "(the consulted agent returned no answer)",
        "agent_loop.workdir_not_allowed": (
            "Agent-loop workdir {path!r} is outside the allowed roots and was ignored."
        ),
        "agent_loop.workdir_unusable": (
            "Agent-loop workdir {path!r} could not be created ({error}); "
            "using the default directory."
        ),
    },
    "zh": {
        "api.content_required": "content 不能为空",
        "api.invalid_channels_config": "渠道配置无效",
        "api.partner_already_exists": "伙伴 '{name}' 已存在",
        "api.partner_not_found": "未找到伙伴",
        "api.partner_not_found_or_not_running": "未找到伙伴或伙伴未运行",
        "api.partner_not_running": "伙伴未运行",
        "api.partner_stopped_start_required": "伙伴已停止。请先启动后再聊天。",
        "api.soul_already_exists": "Soul '{name}' 已存在",
        "api.soul_content_empty": "自定义 soul 内容为空",
        "api.soul_library_not_found": "素材库中未找到 soul '{name}'",
        "api.soul_not_found": "未找到 soul",
        "api.tool_not_found": "未找到工具 '{name}'",
        "cli_apps.abi_mismatch": (
            "CLI 应用 {app!r} 是为 {installed} 安装的，当前运行环境是 {current}，需要管理员重新安装。"
        ),
        "cli_apps.args_required": "{tool} 需要 args 数组：每个命令行参数占一个元素。",
        "cli_apps.entry_admin_only": "CLI 应用由管理员安装，请联系管理员添加。",
        "cli_apps.install_in_progress": "该应用正在安装中。",
        "cli_apps.not_in_catalog": "目录中没有名为 {id!r} 的 CLI 应用。",
        "cli_apps.not_installed": "CLI 应用 {app!r} 已不在本部署中。",
        "cli_apps.still_running": "{app} 仍在运行（已 {seconds} 秒）",
        "mcp.configure_command_or_url": "服务器 {name!r}：请配置 command（stdio）或 url。",
        "mcp.configure_before_testing": "测试前请先配置 command（stdio）或 url。",
        "mcp.server_error": "服务器 {name!r}：{error}",
        "mcp.server_missing": "你的列表中没有名为 {name!r} 的服务器。",
        "mcp.not_oauth": "该服务器不使用 OAuth，请改为填写凭据。",
        "mcp.oauth_callback_incomplete": "授权回调信息不完整。",
        "mcp.oauth_callback_unknown": "该授权已过期或已完成，请重新发起。",
        "mcp.oauth_done": "已授权，可以关闭此页。",
        "mcp.oauth_failed": "授权失败。",
        "mcp.catalog_entry_missing": "目录中没有名为 {id!r} 的 MCP 服务。",
        "mcp.entry_admin_only": "该服务以本地命令方式运行，只能由管理员添加。",
        "mcp.tool_not_available": "该工具在本次对话中不可用，只能调用提示中列出的工具。",
        "chat.stub_notice": (
            "KAGWeb 目前以框架壳模式运行，对话后端尚未接入。模型服务、会话、设置与 "
            "Partners 均已就绪，对话能力由 agent-loop 集成提供"
            '（system.json 的 "agent_loop" 配置块）。'
        ),
        "agent_loop.spawn_failed": "Agent-loop 后端 {backend!r} 启动失败：{error}",
        "agent_loop.exited": "Agent-loop 后端 {backend!r} 异常退出（code {code}）{detail}",
        "agent_loop.timeout": "Agent-loop 后端 {backend!r} 超时（{seconds} 秒）。",
        "agent_loop.http_status": "Agent-loop 后端 {backend!r} 返回 HTTP {status}：{detail}",
        "agent_loop.http_failed": "Agent-loop 后端 {backend!r} 请求失败：{error}",
        "agent_loop.unknown_backend": (
            '未知的 agent-loop 后端 {backend!r}，请检查 "agent_loop" 配置。'
        ),
        "agent_loop.command_required": (
            'Agent-loop 后端 {backend!r} 需要可执行命令，请在 "agent_loop" 配置中设置 "command"。'
        ),
        "agent_loop.url_required": (
            'Agent-loop 后端 {backend!r} 需要服务地址，请在 "agent_loop" 配置中设置 "url"。'
        ),
        "agent_loop.empty_answer": "Agent-loop 后端 {backend!r} 结束但未产生回答。",
        "agent_loop.approval_header": "需要授权",
        "agent_loop.approval_prompt": "智能体请求运行 {tool!r}，是否允许其继续？",
        "agent_loop.approval_decision": "对 {tool!r} 的授权决定：{choice}。",
        "agent_loop.approval_choice_once": "仅允许一次",
        "agent_loop.approval_choice_once_hint": "只批准本次请求",
        "agent_loop.approval_choice_session": "本会话内允许",
        "agent_loop.approval_choice_session_hint": "本会话内的类似请求不再询问",
        "agent_loop.approval_choice_always": "总是允许",
        "agent_loop.approval_choice_always_hint": "记住此授权",
        "agent_loop.approval_choice_deny": "拒绝",
        "agent_loop.approval_choice_deny_hint": "不执行，并告知智能体",
        "agent_loop.clarify_header": "智能体正在询问",
        "agent_loop.clarify_prompt": "智能体需要更多信息才能继续。",
        "agent_loop.clarify_placeholder": "输入你的回答…",
        "agent_loop.clarify_decision": "已把回答发送给智能体。",
        "agent_loop.clarify_skipped": "未作答，智能体将在没有该信息的情况下继续。",
        "agent_loop.request_unsupported": (
            "后端请求了 {kind}（{tool!r}），但该后端无法接收答复，已继续执行。"
        ),
        "agent_loop.acp_too_many_sessions": "活跃的智能体会话过多（上限 {max} 个），请稍后再试。",
        "agent_loop.acp_probe_timeout": "Agent-loop 后端 {backend!r} 未在 {seconds} 秒内完成 ACP 握手。",
        "agent_loop.runs_poll_gave_up": "Agent-loop 后端 {backend!r} 丢失事件流，且轮询期间 run 状态始终未到达终态。",
        "agent_loop.acp_sdk_missing": (
            "后端 {backend!r} 使用 Agent Client Protocol 传输，请先安装 SDK：`pip install kagweb[acp]`。"
        ),
        "agent_loop.runs_no_run_id": "Agent-loop 后端 {backend!r} 启动了 run 但未返回 run_id。",
        "agent_loop.acp_init_failed": "Agent-loop 后端 {backend!r} 的 ACP 握手失败：{error}",
        "agent_loop.acp_turn_failed": "Agent-loop 后端 {backend!r} 在回合中途丢失 ACP 连接：{error}",
        "agent_loop.consult_manifest": (
            "<agent-consultation>\n"
            "以下智能体后端可供咨询：\n"
            "{agents}\n"
            "如果咨询其中之一能实质性地改善回答，请只以下面的围栏代码块结束回复，"
            "块后不要有任何内容：\n"
            "```{fence}\n"
            '{{"agent": "<列表中的后端 id>", "question": "<要提出的问题>"}}\n'
            "```\n"
            "随后你会收到咨询结果，并必须直接回答用户。本回合最多 {budget} 次咨询。"
            "无需咨询时直接回答即可。\n"
            "</agent-consultation>"
        ),
        "agent_loop.consult_followup": (
            "咨询结果已作为最后一条消息附上。请现在给出面向用户的最终回答。"
        ),
        "agent_loop.consult_unknown_agent": ("咨询请求引用了未知智能体 '{agent}'，改为直接回答。"),
        "agent_loop.consult_empty": "（被咨询的智能体未返回内容）",
        "agent_loop.workdir_not_allowed": "Agent-loop 工作目录 {path!r} 不在允许的根目录内，已忽略。",
        "agent_loop.workdir_unusable": (
            "Agent-loop 工作目录 {path!r} 无法创建（{error}），改用默认目录。"
        ),
    },
}


def current_language(default: str = "en") -> str:
    try:
        from kagweb.services.settings.interface_settings import get_ui_language

        return _parse_language(get_ui_language(default=default))
    except Exception:
        return _parse_language(default)


def t(key: str, default: str = "", *, language: str | None = None, **kwargs: Any) -> str:
    lang = _parse_language(language) if language else current_language()
    text = _MESSAGES.get(lang, {}).get(key) or _MESSAGES["en"].get(key) or default
    if kwargs:
        try:
            return text.format(**kwargs)
        except (KeyError, IndexError, ValueError):
            return text
    return text


__all__ = ["current_language", "t"]
