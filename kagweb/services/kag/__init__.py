# -*- coding: utf-8 -*-
"""KAG 集成服务（M1）。

设计：docs/kag-integration-design.md §5.1/§5.3/§6.3（T1 单租户子集）。
职责：读取 ``kag`` settings 域、为 CLI agent-loop 后端生成 session workdir
的 ``.mcp.json``（Claude Code 自动发现）、组装 turn 的 KAG grounding 块。
管理面 REST/UI、多项目路由、per-session token 于 M2/M3 提供。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

MCP_SERVER_NAME = "kag-bridge"


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
    """KAG 接线是否可用：bridge 命令与项目目录均已配置。"""
    block = block if block is not None else get_kag_settings()
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
        "- kag_schema(): read-only SPG type summary of the project.\n"
        "- kag_status(): bridge / OpenSPG server health.\n"
        "Prefer kag_solve for knowledge-graph-grounded questions about the project's domain."
    )


def ensure_session_mcp_config(
    workdir: str, session_id: str, block: dict[str, Any] | None = None
) -> bool:
    """在 session workdir 写入 ``.mcp.json``（CLI 后端发现 bridge，设计 §5.3）。

    M1：实例级 bridge 配置 + ``KAG_SESSION_ID`` 归因（per-session token 于
    M3，附录 A.3）。幂等（每 turn 重写）；失败返回 False，不阻塞 turn。
    """
    if not workdir or not kag_enabled(block):
        return False
    block = block if block is not None else get_kag_settings()
    config = {
        "mcpServers": {
            MCP_SERVER_NAME: {
                "command": str(block.get("bridge_command")),
                "args": [str(a) for a in (block.get("bridge_args") or []) if str(a)],
                "env": {
                    "KAG_PROJECT_DIR": str(block.get("kag_project_dir")),
                    "KAG_SESSION_ID": str(session_id or ""),
                },
            }
        }
    }
    try:
        path = Path(workdir) / ".mcp.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
        # Claude Code 的两道门一并放行（operator 配置 bridge 即授权该工具集，
        # M1 单操作员语义；per-user kag.solve grant 于 M2，设计 §6.4）：
        #   enableAllProjectMcpServers —— 项目级 MCP server 审批（M0-8 实测）
        #   permissions.allow —— 非交互模式下 CLI 的工具级 permission 门
        settings_dir = Path(workdir) / ".claude"
        settings_dir.mkdir(parents=True, exist_ok=True)
        (settings_dir / "settings.json").write_text(
            json.dumps(
                {
                    "enableAllProjectMcpServers": True,
                    "permissions": {
                        "allow": [
                            f"mcp__{MCP_SERVER_NAME}__kag_solve",
                            f"mcp__{MCP_SERVER_NAME}__kag_schema",
                            f"mcp__{MCP_SERVER_NAME}__kag_status",
                        ]
                    },
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        return True
    except OSError:
        return False
