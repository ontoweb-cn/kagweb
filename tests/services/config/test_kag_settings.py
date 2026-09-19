# -*- coding: utf-8 -*-
"""``kag`` system-settings block（设计 docs/kag-integration-design.md §6.3 T1 子集）：
归一化 round-trip、默认值与 ``kag`` 服务的 grounding / .mcp.json 行为。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from kagweb.services.config.runtime_settings import RuntimeSettingsService


def _normalize_kag(raw: dict) -> dict:
    service = RuntimeSettingsService(Path("./nonexistent-settings"), process_env={})
    return service._normalize_system({"kag": raw})["kag"]


def test_defaults_when_block_missing() -> None:
    block = _normalize_kag({})
    assert block["version"] == 1
    assert block["bridge_command"] == ""
    assert block["bridge_args"] == ["-m", "kag_bridge"]
    assert block["kag_project_dir"] == ""
    assert block["bridge_api_key"] == ""
    assert block["bridge_http_url"] == ""
    assert block["service_user_no"] == ""


def test_service_user_no_roundtrips() -> None:
    """评审修复：settings 域可配 service_user_no（无用户上下文的系统调用归因）。"""
    assert _normalize_kag({"service_user_no": " kag-svc "})["service_user_no"] == "kag-svc"
    assert _normalize_kag({"service_user_no": ""})["service_user_no"] == ""


def test_normalize_strips_and_keeps_fields() -> None:
    block = _normalize_kag(
        {
            "bridge_command": " /usr/bin/python3 ",
            "bridge_args": ["-m", " kag_bridge ", ""],
            "kag_project_dir": "/srv/kag/proj",
            "namespace": " m0ProbeLive ",
            "project_id": 3,  # 数字会被字符串化
            "spg_server_url": "http://127.0.0.1:8887",
            "bridge_api_key": "secret",
            "bridge_http_url": " http://127.0.0.1:8890 ",
        }
    )
    assert block["bridge_command"] == "/usr/bin/python3"
    assert block["bridge_args"] == ["-m", "kag_bridge"]  # 空项被剔除
    assert block["kag_project_dir"] == "/srv/kag/proj"
    assert block["namespace"] == "m0ProbeLive"
    assert block["project_id"] == "3"
    assert block["spg_server_url"] == "http://127.0.0.1:8887"
    assert block["bridge_api_key"] == "secret"
    assert block["bridge_http_url"] == "http://127.0.0.1:8890"


def test_empty_args_falls_back_to_default() -> None:
    # 显式空列表归一回默认（防呆：空 args 的 bridge 配置几乎必为误配）
    block = _normalize_kag({"bridge_command": "/x/y", "bridge_args": []})
    assert block["bridge_args"] == ["-m", "kag_bridge"]


def test_non_dict_block_tolerated() -> None:
    assert _normalize_kag("nonsense")["version"] == 1  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# kag 服务：grounding 与 session .mcp.json
# ---------------------------------------------------------------------------

BLOCK = {
    "bridge_command": "/venv/bin/python",
    "bridge_args": ["-m", "kag_bridge"],
    "kag_project_dir": "/srv/kag/proj",
    "namespace": "m0ProbeLive",
    "project_id": "3",
}

HTTP_BLOCK = {
    "bridge_http_url": "http://127.0.0.1:8890",
    "bridge_api_key": "instance-key",
    "namespace": "m0ProbeLive",
}


def test_kag_enabled_requires_command_and_project_dir() -> None:
    from kagweb.services.kag import kag_enabled

    assert kag_enabled(BLOCK) is True
    assert kag_enabled({**BLOCK, "bridge_command": ""}) is False
    assert kag_enabled({**BLOCK, "kag_project_dir": ""}) is False
    assert kag_enabled({}) is False


def test_kag_enabled_http_form_needs_url_and_key() -> None:
    """M3.6：http 形态只需 bridge_http_url + bridge_api_key（stdio 项可全空）。"""
    from kagweb.services.kag import kag_enabled

    assert kag_enabled(HTTP_BLOCK) is True
    # 有 url 无 key → 不可用（无法换 token）
    assert kag_enabled({**HTTP_BLOCK, "bridge_api_key": ""}) is False


def test_grounding_block_content_and_disabled() -> None:
    from kagweb.services.kag import kag_grounding_block

    text = kag_grounding_block(BLOCK)
    assert text.startswith("[KAG integration]")
    assert "kag_solve(question)" in text
    assert "kag_reason(dsl, params)" in text  # M3.3 工具进 grounding 说明
    assert "m0ProbeLive" in text
    # 无状态语义（设计 R2）必须写进工具说明
    assert "Stateless single-turn" in text
    assert kag_grounding_block({}) == ""
    assert kag_grounding_block({**BLOCK, "bridge_command": ""}) == ""
    # http 形态同样出 grounding 块
    assert kag_grounding_block(HTTP_BLOCK).startswith("[KAG integration]")


def test_ensure_session_mcp_config_writes_files(tmp_path: Path) -> None:
    from kagweb.services.kag import MCP_SERVER_NAME, ensure_session_mcp_config

    workdir = tmp_path / "ws"
    assert asyncio.run(ensure_session_mcp_config(str(workdir), "sess-1", BLOCK)) is True

    mcp_cfg = json.loads((workdir / ".mcp.json").read_text(encoding="utf-8"))
    server = mcp_cfg["mcpServers"][MCP_SERVER_NAME]
    assert server["command"] == "/venv/bin/python"
    assert server["args"] == ["-m", "kag_bridge"]
    assert server["env"]["KAG_PROJECT_DIR"] == "/srv/kag/proj"
    assert server["env"]["KAG_SESSION_ID"] == "sess-1"

    settings = json.loads((workdir / ".claude" / "settings.json").read_text(encoding="utf-8"))
    assert settings["enableAllProjectMcpServers"] is True
    allow = settings["permissions"]["allow"]
    assert f"mcp__{MCP_SERVER_NAME}__kag_solve" in allow
    # M3.3 遗漏修复：kag_reason 也在 permission 门放行列表
    assert f"mcp__{MCP_SERVER_NAME}__kag_reason" in allow


def test_ensure_session_mcp_config_http_form_writes_token_bearer(
    tmp_path: Path, monkeypatch
) -> None:
    """M3.6：http 形态 .mcp.json 携带 per-session token，实例 key 不落盘。"""
    from kagweb.services import kag as kag_service

    async def fake_exchange(http_url, block, session_id):
        assert http_url == "http://127.0.0.1:8890"
        assert session_id == "sess-http"
        return "kagt.c3NzaW9uMWIwMDAwLjEyMw0K.sig"

    monkeypatch.setattr(kag_service, "_exchange_session_token", fake_exchange)
    workdir = tmp_path / "ws"
    assert asyncio.run(
        kag_service.ensure_session_mcp_config(str(workdir), "sess-http", HTTP_BLOCK)
    ) is True

    mcp_cfg = json.loads((workdir / ".mcp.json").read_text(encoding="utf-8"))
    server = mcp_cfg["mcpServers"]["kag-bridge"]
    assert server["type"] == "http"
    # 约定 bridge_http_url 为根 URL，MCP 端点拼默认 /mcp
    assert server["url"] == "http://127.0.0.1:8890/mcp"
    assert server["headers"]["Authorization"] == "Bearer kagt.c3NzaW9uMWIwMDAwLjEyMw0K.sig"
    # 实例 key 与 stdio 专有键绝不出现
    dumped = json.dumps(mcp_cfg)
    assert "instance-key" not in dumped
    assert "command" not in server
    assert "env" not in server


def test_ensure_session_mcp_config_token_failure_degrades(
    tmp_path: Path, monkeypatch
) -> None:
    """token 换取失败（bridge 不在线/鉴权失败）→ False 且不写任何文件，
    不回落 stdio（operator 只配 http 时回落只会生成无法工作的配置）。"""
    from kagweb.services import kag as kag_service

    async def dead_exchange(http_url, block, session_id):
        return ""

    monkeypatch.setattr(kag_service, "_exchange_session_token", dead_exchange)
    workdir = tmp_path / "ws"
    assert asyncio.run(
        kag_service.ensure_session_mcp_config(str(workdir), "s", HTTP_BLOCK)
    ) is False
    assert not (workdir / ".mcp.json").exists()
    assert not (workdir / ".claude" / "settings.json").exists()


def test_ensure_session_mcp_config_merges_existing_settings(tmp_path: Path) -> None:
    """已有 settings.json 只合并管辖键，不整体覆盖（评审 M1）。"""
    from kagweb.services.kag import ensure_session_mcp_config

    workdir = tmp_path / "ws"
    workdir.mkdir()
    (workdir / ".claude").mkdir()
    (workdir / ".claude" / "settings.json").write_text(
        json.dumps(
            {
                "permissions": {"allow": ["Bash(ls:*)"], "deny": ["WebFetch"]},
                "custom_key": {"kept": True},
            }
        ),
        encoding="utf-8",
    )

    assert asyncio.run(ensure_session_mcp_config(str(workdir), "s", BLOCK)) is True
    settings = json.loads((workdir / ".claude" / "settings.json").read_text(encoding="utf-8"))
    assert settings["custom_key"] == {"kept": True}  # 未管辖键保留
    assert "Bash(ls:*)" in settings["permissions"]["allow"]  # 既有权限保留
    assert settings["permissions"]["deny"] == ["WebFetch"]
    assert "mcp__kag-bridge__kag_solve" in settings["permissions"]["allow"]


def test_ensure_session_mcp_config_disabled_or_invalid(tmp_path: Path) -> None:
    from kagweb.services.kag import ensure_session_mcp_config

    # 未启用（bridge 未配置）→ 不写任何文件
    workdir = tmp_path / "ws"
    assert asyncio.run(ensure_session_mcp_config(str(workdir), "s", {})) is False
    assert not (workdir / ".mcp.json").exists()
    # 空 workdir → False
    assert asyncio.run(ensure_session_mcp_config("", "s", BLOCK)) is False
