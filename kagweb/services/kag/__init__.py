# -*- coding: utf-8 -*-
"""KAG 集成服务（M1）。

设计：docs/kag-integration-design.md §5.1/§5.3/§6.3（T1 单租户子集）。
职责：读取 ``kag`` settings 域、为 CLI agent-loop 后端生成 session workdir
的 ``.mcp.json``（Claude Code 自动发现）、组装 turn 的 KAG grounding 块。
管理面 REST/UI 于 M2 提供；M3.6：http 形态 bridge 经 ``POST /tokens`` 换
per-session 短期 token（实例 key 不进 workdir，附录 A.3）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

MCP_SERVER_NAME = "kag-bridge"

#: Claude Code 工具级 permission 门放行的 bridge 工具面（M3.3 起含 kag_reason）
_MCP_ALLOWED_TOOLS = (
    "kag_solve",
    "kag_schema",
    "kag_status",
    "kag_reason",
)


def get_kag_settings() -> dict[str, Any]:
    """读取归一化后的 ``kag`` settings 块（异常/缺省返回空 dict，不阻塞 turn）。"""
    try:
        from kagweb.services.config.runtime_settings import RuntimeSettingsService

        system = RuntimeSettingsService.get_instance().load_system()
    except Exception:
        return {}
    block = system.get("kag")
    return dict(block) if isinstance(block, dict) else {}


def kag_enabled(block: dict[str, Any] | None = None) -> bool:
    """KAG 接线是否可用：http 形态（bridge_http_url + key）或 stdio 形态
    （bridge_command + 项目目录）二者居一（M3.6 起 http 与 stdio 平行）。"""
    block = block if block is not None else get_kag_settings()
    if str(block.get("bridge_http_url") or "").strip():
        return bool(str(block.get("bridge_api_key") or "").strip())
    return bool(str(block.get("bridge_command") or "").strip()) and bool(
        str(block.get("kag_project_dir") or "").strip()
    )


def kag_grounding_block(block: dict[str, Any] | None = None) -> str:
    """注入 agent loop prompt 的 KAG grounding 块（未启用时为空串）。

    无状态语义（设计 R2）：明确告知 agent 需把对话上下文拼进 question。
    """
    block = block if block is not None else get_kag_settings()
    if not kag_enabled(block):
        return ""
    namespace = str(block.get("namespace") or "").strip() or "run kag_status to inspect"
    return (
        "[KAG integration]\n"
        "MCP server `kag-bridge` is configured for this session and provides:\n"
        "- kag_solve(question): LLM-augmented reasoning over the bound KAG project "
        f"(namespace: {namespace}). Stateless single-turn — include any needed "
        "conversation context inside `question`.\n"
        "- kag_reason(dsl, params): read-only reason DSL graph query returning a "
        "table (node types must use the namespace-qualified full name; the tool "
        "normalizes params values, so raw arrays are accepted).\n"
        "- kag_schema(): read-only SPG type summary of the project.\n"
        "- kag_status(): bridge / OpenSPG server health.\n"
        "Prefer kag_solve for knowledge-graph-grounded questions about the project's domain."
    )


async def _exchange_session_token(
    http_url: str, block: dict[str, Any], session_id: str
) -> str:
    """向 bridge ``POST /tokens`` 换 per-session 短期 token（M3.6，附录 A.3）。

    实例 key 只在本函数（服务端内存）使用，不写入 workdir；失败返回空串
    （调用方按"本 turn 无 KAG 工具"降级，不阻塞 turn）。TTL 用 bridge 缺省
    （900s）——.mcp.json 每 turn 重写即持续刷新。超时 30s：bridge 首次
    ``/tokens`` 触发 KAG 延迟加载（冷启动秒级），10s 余量不足（评审 M-4）。
    """
    key = str(block.get("bridge_api_key") or "").strip()
    if not key:
        return ""
    try:
        import httpx

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{http_url.rstrip('/')}/tokens",
                json={"session_id": str(session_id or "")},
                headers={"Authorization": f"Bearer {key}"},
            )
        if resp.status_code != 200:
            return ""
        return str((resp.json() or {}).get("token") or "")
    except Exception:
        return ""


async def ensure_session_mcp_config(
    workdir: str, session_id: str, block: dict[str, Any] | None = None
) -> bool:
    """在 session workdir 写入 ``.mcp.json``（CLI 后端发现 bridge，设计 §5.3）。

    两种形态（M3.6）：``bridge_http_url`` 配置时生成 http server（Bearer 为
    per-session token——换取失败即返回 False，不回落 stdio：operator 只配了
    http 时回落只会生成无法工作的 stdio 配置）；否则维持 stdio（env 仅
    ``KAG_PROJECT_DIR``/``KAG_SESSION_ID``，进程级信任，M1 语义）。幂等（每
    turn 重写）；失败返回 False，不阻塞 turn。
    """
    if not workdir or not kag_enabled(block):
        return False
    block = block if block is not None else get_kag_settings()
    http_url = str(block.get("bridge_http_url") or "").strip()
    if http_url:
        token = await _exchange_session_token(http_url, block, str(session_id or ""))
        if not token:
            return False
        server: dict[str, Any] = {
            "type": "http",
            # 约定 bridge_http_url 为 bridge 根（无路径），MCP 端点拼默认
            # /mcp（与 bridge 的 KAG_BRIDGE_HTTP_PATH 缺省一致——自定义
            # path 时两侧需同步，见 settings 注释）。
            "url": f"{http_url.rstrip('/')}/mcp",
            "headers": {"Authorization": f"Bearer {token}"},
        }
    else:
        server = {
            "command": str(block.get("bridge_command")),
            "args": [str(a) for a in (block.get("bridge_args") or []) if str(a)],
            "env": {
                "KAG_PROJECT_DIR": str(block.get("kag_project_dir")),
                "KAG_SESSION_ID": str(session_id or ""),
            },
        }
    config = {"mcpServers": {MCP_SERVER_NAME: server}}
    try:
        path = Path(workdir) / ".mcp.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
        # Claude Code 的两道门一并放行（operator 配置 bridge 即授权该工具集，
        # M1 单操作员语义；per-user kag.solve grant 于 M2，设计 §6.4）：
        #   enableAllProjectMcpServers —— 项目级 MCP server 审批（M0-8 实测）
        #   permissions.allow —— 非交互模式下 CLI 的工具级 permission 门
        # 与已有内容合并（workdir 内可能存在 agent/用户自建的 settings.json，
        # 只更新我们管辖的键，不整体覆盖）。
        settings_path = Path(workdir) / ".claude" / "settings.json"
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        existing: dict[str, Any] = {}
        try:
            parsed = json.loads(settings_path.read_text(encoding="utf-8"))
            if isinstance(parsed, dict):
                existing = parsed
        except (OSError, ValueError):
            existing = {}
        allow = set(existing.get("permissions", {}).get("allow", []) or [])
        allow.update(f"mcp__{MCP_SERVER_NAME}__{tool}" for tool in _MCP_ALLOWED_TOOLS)
        merged = dict(existing)
        merged["enableAllProjectMcpServers"] = True
        merged["permissions"] = {**existing.get("permissions", {}), "allow": sorted(allow)}
        settings_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
        return True
    except OSError:
        return False
