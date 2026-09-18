# -*- coding: utf-8 -*-
"""``kag`` system-settings block（设计 docs/kag-integration-design.md §6.3 T1 子集）：
归一化 round-trip、默认值与 ``kag`` 服务的 grounding / .mcp.json 行为。"""

from __future__ import annotations

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
        }
    )
    assert block["bridge_command"] == "/usr/bin/python3"
    assert block["bridge_args"] == ["-m", "kag_bridge"]  # 空项被剔除
    assert block["kag_project_dir"] == "/srv/kag/proj"
    assert block["namespace"] == "m0ProbeLive"
    assert block["project_id"] == "3"
    assert block["spg_server_url"] == "http://127.0.0.1:8887"
    assert block["bridge_api_key"] == "secret"


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


def test_kag_enabled_requires_command_and_project_dir() -> None:
    from kagweb.services.kag import kag_enabled

    assert kag_enabled(BLOCK) is True
    assert kag_enabled({**BLOCK, "bridge_command": ""}) is False
    assert kag_enabled({**BLOCK, "kag_project_dir": ""}) is False
    assert kag_enabled({}) is False


def test_grounding_block_content_and_disabled() -> None:
    from kagweb.services.kag import kag_grounding_block

    text = kag_grounding_block(BLOCK)
    assert text.startswith("[KAG integration]")
    assert "kag_solve(question)" in text
    assert "m0ProbeLive" in text
    # 无状态语义（设计 R2）必须写进工具说明
    assert "Stateless single-turn" in text
    assert kag_grounding_block({}) == ""
    assert kag_grounding_block({**BLOCK, "bridge_command": ""}) == ""


def test_ensure_session_mcp_config_writes_files(tmp_path: Path) -> None:
    from kagweb.services.kag import MCP_SERVER_NAME, ensure_session_mcp_config

    workdir = tmp_path / "ws"
    assert ensure_session_mcp_config(str(workdir), "sess-1", BLOCK) is True

    mcp_cfg = json.loads((workdir / ".mcp.json").read_text(encoding="utf-8"))
    server = mcp_cfg["mcpServers"][MCP_SERVER_NAME]
    assert server["command"] == "/venv/bin/python"
    assert server["args"] == ["-m", "kag_bridge"]
    assert server["env"]["KAG_PROJECT_DIR"] == "/srv/kag/proj"
    assert server["env"]["KAG_SESSION_ID"] == "sess-1"

    settings = json.loads((workdir / ".claude" / "settings.json").read_text(encoding="utf-8"))
    assert settings["enableAllProjectMcpServers"] is True
    assert f"mcp__{MCP_SERVER_NAME}__kag_solve" in settings["permissions"]["allow"]


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

    assert ensure_session_mcp_config(str(workdir), "s", BLOCK) is True
    settings = json.loads((workdir / ".claude" / "settings.json").read_text(encoding="utf-8"))
    assert settings["custom_key"] == {"kept": True}  # 未管辖键保留
    assert "Bash(ls:*)" in settings["permissions"]["allow"]  # 既有权限保留
    assert settings["permissions"]["deny"] == ["WebFetch"]
    assert "mcp__kag-bridge__kag_solve" in settings["permissions"]["allow"]


def test_ensure_session_mcp_config_disabled_or_invalid(tmp_path: Path) -> None:
    from kagweb.services.kag import ensure_session_mcp_config

    # 未启用（bridge 未配置）→ 不写任何文件
    workdir = tmp_path / "ws"
    assert ensure_session_mcp_config(str(workdir), "s", {}) is False
    assert not (workdir / ".mcp.json").exists()
    # 空 workdir → False
    assert ensure_session_mcp_config("", "s", BLOCK) is False
