"""``agent_loop`` system-settings block: normalization and env overrides."""

from __future__ import annotations

from pathlib import Path

from kagweb.services.config.runtime_settings import RuntimeSettingsService


def _normalize(raw: dict) -> dict:
    service = RuntimeSettingsService(Path("./nonexistent-settings"), process_env={})
    return service._normalize_system({"agent_loop": raw})["agent_loop"]


def test_defaults_when_block_missing() -> None:
    block = _normalize({})
    assert block["backend"] == ""
    assert block["timeout_seconds"] == 900
    assert block["session_workspace"] is True
    assert block["turn_path"] == "/agent/turn"
    assert block["args"] == [] and block["env"] == {} and block["headers"] == {}


def test_normalization_coerces_and_clamps() -> None:
    block = _normalize(
        {
            "backend": " claude-code ",
            "command": "claude",
            "args": ["--x", "", 3],
            "env": {"A": "1", "": "dropped", "B": None},
            "url": "http://agent/",
            "turn_path": "custom/path",
            "headers": {"X-Trace": "t"},
            "api_key": "k",
            "timeout_seconds": 5,  # clamped up to the floor
            "session_workspace": "false",
        }
    )
    assert block["backend"] == "claude-code"
    assert block["args"] == ["--x", "3"]
    assert block["env"] == {"A": "1", "B": "None"}
    assert block["url"] == "http://agent/"
    assert block["turn_path"] == "custom/path"
    assert block["headers"] == {"X-Trace": "t"}
    assert block["timeout_seconds"] == 30
    assert block["session_workspace"] is False


def test_env_overrides_apply(tmp_path: Path) -> None:
    service = RuntimeSettingsService(
        tmp_path,
        process_env={
            "KAGWEB_AGENT_LOOP_BACKEND": "hermes",
            "KAGWEB_AGENT_LOOP_URL": "http://hermes:9000",
            "KAG_AGENT_LOOP_API_KEY": "sk-test",
        },
    )
    block = service.load_system()["agent_loop"]
    assert block["backend"] == "hermes"
    assert block["url"] == "http://hermes:9000"
    assert block["api_key"] == "sk-test"


def test_kagweb_prefixed_api_key_no_longer_applies(tmp_path: Path) -> None:
    """The credential override moved to the KAG_ prefix; the old name is inert."""
    service = RuntimeSettingsService(
        tmp_path,
        process_env={"KAGWEB_AGENT_LOOP_API_KEY": "stale"},
    )
    block = service.load_system()["agent_loop"]
    assert block["api_key"] == ""
