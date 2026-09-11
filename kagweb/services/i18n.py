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
