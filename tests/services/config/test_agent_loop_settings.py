"""``agent_loop`` system-settings block: v2 normalization, v1 migration,
auto-primary resolution, and env overrides."""

from __future__ import annotations

from pathlib import Path

from kagweb.services.config.runtime_settings import RuntimeSettingsService


def _normalize_agent_loop(raw: dict) -> dict:
    service = RuntimeSettingsService(Path("./nonexistent-settings"), process_env={})
    return service._normalize_system({"agent_loop": raw})["agent_loop"]


def test_defaults_when_block_missing() -> None:
    block = _normalize_agent_loop({})
    assert block["version"] == 2
    assert block["profiles"] == []
    assert block["primary"] == ""
    assert block["consult_budget"] == 3


def test_v1_flat_block_migrates_into_default_profile() -> None:
    block = _normalize_agent_loop(
        {
            "backend": " claude-code ",
            "command": "claude",
            "args": ["--x", "", 3],
            "env": {"A": "1", "": "dropped"},
            "url": "http://agent/",
            "turn_path": "custom/path",
            "headers": {"X-Trace": "t"},
            "api_key": "k",
            "timeout_seconds": 5,  # clamped up to the floor
            "session_workspace": "false",
        }
    )
    assert len(block["profiles"]) == 1
    profile = block["profiles"][0]
    assert profile["id"] == "default"
    assert profile["preset"] == "claude-code"
    assert profile["enabled"] is True
    assert profile["args"] == ["--x", "3"]
    assert profile["env"] == {"A": "1"}
    assert profile["url"] == "http://agent/"
    assert profile["turn_path"] == "custom/path"
    assert profile["headers"] == {"X-Trace": "t"}
    assert profile["timeout_seconds"] == 30
    assert profile["session_workspace"] is False
    assert block["primary"] == "default"


def test_profiles_normalize_and_clamp() -> None:
    block = _normalize_agent_loop(
        {
            "profiles": [
                {"id": " b ", "name": "  ", "preset": "hermes", "enabled": "false"},
                {"id": "b", "preset": "agentscope", "timeout_seconds": 10**9},
            ],
            "primary": "b",
            "consult_budget": 99,
        }
    )
    first, second = block["profiles"]
    assert first["id"] == "b"
    assert first["name"] == "hermes"  # falls back to the preset label
    assert first["enabled"] is False
    assert second["id"] == "b-2"  # duplicate ids are deduped
    assert second["timeout_seconds"] == 86_400
    assert block["consult_budget"] == 12


def test_auto_primary_prefers_local_intellect() -> None:
    block = _normalize_agent_loop(
        {
            "profiles": [
                {"id": "remote", "preset": "hermes", "url": "https://remote", "enabled": True},
                {
                    "id": "local-intellect",
                    "preset": "intellect-team",
                    "url": "http://127.0.0.1:8083",
                },
                {"id": "local-cli", "preset": "claude-code"},
            ]
        }
    )
    assert block["primary"] == "local-intellect"


def test_auto_primary_falls_back_to_any_local_then_first_enabled() -> None:
    no_intellect = _normalize_agent_loop(
        {
            "profiles": [
                {"id": "remote", "preset": "hermes", "url": "https://remote"},
                {"id": "local-cli", "preset": "opencode"},
            ]
        }
    )
    assert no_intellect["primary"] == "local-cli"

    only_remote = _normalize_agent_loop(
        {"profiles": [{"id": "remote", "preset": "hermes", "url": "https://remote"}]}
    )
    assert only_remote["primary"] == "remote"

    none_enabled = _normalize_agent_loop(
        {"profiles": [{"id": "x", "preset": "hermes", "url": "https://x", "enabled": False}]}
    )
    assert none_enabled["primary"] == ""


def test_explicit_primary_semantics() -> None:
    # "" is the user's explicit shell-stub choice — never auto-overridden.
    block = _normalize_agent_loop(
        {
            "profiles": [{"id": "li", "preset": "intellect", "url": "http://localhost:1"}],
            "primary": "",
        }
    )
    assert block["primary"] == ""

    # A dangling pointer is healed by the default rule.
    healed = _normalize_agent_loop(
        {
            "profiles": [{"id": "li", "preset": "intellect", "url": "http://localhost:1"}],
            "primary": "gone",
        }
    )
    assert healed["primary"] == "li"


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
    assert block["primary"] == "env-override"
    (profile,) = block["profiles"]
    assert profile["preset"] == "hermes"
    assert profile["url"] == "http://hermes:9000"
    assert profile["api_key"] == "sk-test"


def test_env_overrides_pin_the_primary_profile(tmp_path: Path) -> None:
    service = RuntimeSettingsService(
        tmp_path,
        process_env={
            "KAGWEB_AGENT_LOOP_BACKEND": "hermes",
            "KAGWEB_AGENT_LOOP_URL": "http://h:9000",
        },
    )
    service.save_system(
        {
            "agent_loop": {
                "version": 2,
                "profiles": [{"id": "li", "preset": "intellect", "url": "http://localhost:1"}],
                "primary": "li",
            }
        }
    )
    block = service.load_system()["agent_loop"]
    (profile,) = block["profiles"]
    assert profile["id"] == "li"
    assert profile["preset"] == "hermes"  # the env pins the primary in place
    assert profile["url"] == "http://h:9000"
    assert block["primary"] == "li"


def test_workdir_roots_default_when_missing_but_honour_an_empty_list() -> None:
    """Absent means "not configured" → default; explicitly empty means
    "no per-profile workdirs" and must survive normalization."""
    assert _normalize_agent_loop({})["allowed_workdir_roots"] == ["data/user"]
    assert _normalize_agent_loop({"allowed_workdir_roots": []})["allowed_workdir_roots"] == []
    assert _normalize_agent_loop({"allowed_workdir_roots": ["  D:/p  ", ""]})[
        "allowed_workdir_roots"
    ] == ["D:/p"]


def test_env_override_preserves_the_workdir_allowlist(tmp_path: Path) -> None:
    """The override path rebuilds the block; dropping the roots there would
    silently re-default a widened allowlist in env-pinned deployments."""
    service = RuntimeSettingsService(
        tmp_path,
        process_env={"KAGWEB_AGENT_LOOP_BACKEND": "custom-cli"},
    )
    stored = service.load_system(include_process_overrides=False)
    service.save_system(
        {
            **stored,
            "agent_loop": {
                "version": 2,
                "profiles": [],
                "primary": "",
                "consult_budget": 3,
                "allowed_workdir_roots": ["D:/projects"],
            },
        }
    )
    block = service.load_system(include_process_overrides=True)["agent_loop"]
    assert block["allowed_workdir_roots"] == ["D:/projects"]


def test_kagweb_prefixed_api_key_no_longer_applies(tmp_path: Path) -> None:
    """The credential override moved to the KAG_ prefix; the old name is inert."""
    service = RuntimeSettingsService(
        tmp_path,
        process_env={"KAGWEB_AGENT_LOOP_API_KEY": "stale"},
    )
    block = service.load_system()["agent_loop"]
    assert block["profiles"] == []


def test_approval_policy_fields_normalize() -> None:
    """New approval-policy profile fields: defaults, clamping, unknown values."""
    block = _normalize_agent_loop(
        {
            "profiles": [
                {"id": "a", "preset": "claude-code"},
                {
                    "id": "b",
                    "preset": "codex",
                    "approval_timeout_seconds": 999,
                    "approval_default": "ALWAYS",
                },
                {
                    "id": "c",
                    "preset": "opencode",
                    "approval_timeout_seconds": 1,
                    "approval_default": "banana",
                },
            ],
        }
    )
    first, second, third = block["profiles"]
    assert first["approval_timeout_seconds"] == 60
    assert first["approval_default"] == "deny"
    assert second["approval_timeout_seconds"] == 600  # clamped to the ceiling
    assert second["approval_default"] == "always"  # value passes through lowered
    assert third["approval_timeout_seconds"] == 5  # clamped to the floor
    assert third["approval_default"] == "deny"  # unknown choice refused


def test_intellect_url_profile_migrates_to_custom_http() -> None:
    """The community `intellect` preset is the ACP transport now; a legacy
    profile that configured it as a URL service keeps working as custom-http
    instead of silently becoming a CLI spawn."""
    block = _normalize_agent_loop(
        {"profiles": [{"id": "li", "preset": "intellect", "url": "http://localhost:8083"}]}
    )
    profile = block["profiles"][0]
    assert profile["preset"] == "custom-http"
    assert profile["url"] == "http://localhost:8083"
    # An intellect profile WITHOUT a url (a fresh operator profile for the
    # ACP transport) is left alone.
    block2 = _normalize_agent_loop({"profiles": [{"id": "a", "preset": "intellect"}]})
    assert block2["profiles"][0]["preset"] == "intellect"


def test_reserved_picker_ids_are_regenerated() -> None:
    """The primary picker uses ``__auto__`` / ``__none__`` as mode sentinels, so
    a profile carrying one as its id would render a second radio with the same
    key and the same ``checked`` condition as the sentinel."""
    block = _normalize_agent_loop(
        {
            "profiles": [
                {"id": "__none__", "preset": "claude-code"},
                {"id": "__auto__", "preset": "codex"},
                {"id": "claude-local", "preset": "claude-code"},
            ],
            "primary": "__none__",
        }
    )

    assert [profile["id"] for profile in block["profiles"]] == [
        "profile-1",
        "profile-2",
        "claude-local",
    ]
    # The dangling sentinel primary is healed by the auto rule.
    assert block["primary"] not in {"__auto__", "__none__"}


def test_model_and_context_window_normalize() -> None:
    """The two fields that let an operator describe the backend's model.

    `context_window` is the interesting one: 0 means "not configured" and must
    survive normalization, because the budget planner treats it as absent — a
    clamp to the floor would silently claim a 1,024-token window.
    """
    block = _normalize_agent_loop(
        {
            "profiles": [
                {"id": "a", "preset": "claude-code"},
                {"id": "b", "preset": "claude-code", "model": "  claude-sonnet-5  "},
                {"id": "c", "preset": "claude-code", "context_window": 200000},
                {"id": "d", "preset": "claude-code", "context_window": 5},
                {"id": "e", "preset": "claude-code", "context_window": 99_999_999},
                {"id": "f", "preset": "claude-code", "context_window": "abc"},
            ],
        }
    )
    by_id = {profile["id"]: profile for profile in block["profiles"]}

    assert by_id["a"]["model"] == ""
    assert by_id["a"]["context_window"] == 0  # unset stays unset

    assert by_id["b"]["model"] == "claude-sonnet-5"  # trimmed

    assert by_id["c"]["context_window"] == 200000
    assert by_id["d"]["context_window"] == 1_024  # below floor → clamped up
    assert by_id["e"]["context_window"] == 1_000_000  # above ceiling → clamped down
    assert by_id["f"]["context_window"] == 0  # unparseable → unset, not floored
