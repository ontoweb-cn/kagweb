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
    # A profile that explicitly selected the preset's own HTTP transport also
    # speaks a different protocol (/v1/runs), so it must not be folded into
    # the URL-shaped legacy rewrite above.
    block3 = _normalize_agent_loop(
        {
            "profiles": [
                {
                    "id": "runs",
                    "preset": "intellect",
                    "transport": "http",
                    "url": "http://localhost:8642",
                }
            ]
        }
    )
    assert block3["profiles"][0]["preset"] == "intellect"
    assert block3["profiles"][0]["transport"] == "http"


def test_transport_normalizes_to_the_presets_defaults() -> None:
    """``transport`` is stored resolved, and only where a choice exists.

    Single-transport presets keep "" so the config file does not grow a field
    that means nothing. For a preset that does offer a choice the default is
    written out, so the file states which backend a profile builds without the
    reader having to know today's default. An id this build does not know is
    rewritten to that default rather than persisted as an unresolvable
    selector — the turn path refuses on unknown selectors (correct for a
    hand-edited file), and a file this build just wrote must not become one.
    """
    block = _normalize_agent_loop(
        {
            "profiles": [
                {"id": "bare", "preset": "intellect"},
                {"id": "acp", "preset": "intellect", "transport": "acp"},
                {"id": "junk", "preset": "intellect", "transport": "nonsense"},
                {"id": "cli", "preset": "claude-code", "transport": "http"},
            ]
        }
    )
    by_id = {profile["id"]: profile for profile in block["profiles"]}
    assert by_id["bare"]["transport"] == "acp"
    assert by_id["acp"]["transport"] == "acp"
    assert by_id["junk"]["transport"] == "acp"
    assert by_id["cli"]["transport"] == ""


def test_env_transport_override_pins_the_preset_transport() -> None:
    """A containerized deployment reaches Intellect over HTTP by env alone."""
    service = RuntimeSettingsService(
        Path("./nonexistent-settings"),
        process_env={
            "KAGWEB_AGENT_LOOP_BACKEND": "intellect",
            "KAGWEB_AGENT_LOOP_TRANSPORT": "http",
            "KAGWEB_AGENT_LOOP_URL": "http://gw:8642",
        },
    )
    block = service.load_system().get("agent_loop") or {}
    primary = next(profile for profile in block["profiles"] if profile["id"] == block["primary"])
    assert primary["preset"] == "intellect"
    assert primary["transport"] == "http"
    assert primary["url"] == "http://gw:8642"


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


def test_identity_mode_normalizes_and_never_widens() -> None:
    """How a turn is attributed to a remote service.

    The safe direction matters here: an unrecognized value must land on `off`
    (send nothing extra), never on one of the modes that forwards identity — a
    typo should not silently turn on credential delegation for every turn.

    An *absent* value is not a typo, though: it means "whatever this preset
    does by default", which for the self-hosted Intellect services is
    attribution (the same shape the sibling enterprise UI sends). Every other
    preset still sends nothing.
    """
    block = _normalize_agent_loop(
        {
            "profiles": [
                {"id": "a", "preset": "intellect-team", "identity_mode": "token"},
                {"id": "b", "preset": "intellect-team", "identity_mode": "  HEADER  "},
                {"id": "c", "preset": "intellect-team", "identity_mode": "token_required"},
                {"id": "d", "preset": "intellect-team", "identity_mode": "tokn"},
                {"id": "e", "preset": "intellect-team"},
                {"id": "f", "preset": "intellect-team", "identity_mode": ""},
                {
                    "id": "g",
                    "preset": "intellect",
                    "transport": "http",
                    "url": "http://127.0.0.1:8642",
                },
                {"id": "h", "preset": "hermes", "url": "https://h.example"},
                {
                    "id": "i",
                    "preset": "intellect",
                    "transport": "acp",
                    "identity_mode": "token",
                },
            ],
        }
    )
    by_id = {profile["id"]: profile for profile in block["profiles"]}

    assert by_id["a"]["identity_mode"] == "token"
    assert by_id["b"]["identity_mode"] == "header"  # case and space folded
    assert by_id["c"]["identity_mode"] == "token_required"
    assert by_id["d"]["identity_mode"] == "off"  # a typo sends nothing
    assert by_id["e"]["identity_mode"] == "header"  # absent = the preset default
    assert by_id["f"]["identity_mode"] == "header"
    assert by_id["g"]["identity_mode"] == "header"  # community over HTTP too
    assert by_id["h"]["identity_mode"] == "off"  # a service that knows nothing
    # The local ACP child presents no headers at all, so the default is nothing
    # — and an explicit mode on it is not silently rewritten, either.
    assert by_id["i"]["identity_mode"] == "token"


def test_turn_path_falls_back_to_the_presets_own_endpoint() -> None:
    """A profile that names no path inherits the *transport's* default.

    The generic ``/agent/turn`` is only correct for the presets that speak
    KAGWeb's own turn contract. Both Intellect HTTP presets speak the run
    channel, and this normalizer used to overwrite their ``/v1/runs`` with the
    generic path on the way to disk — which made every deployment of them POST
    an endpoint the service does not expose. The preset layer is where the path
    is declared, so the resolved value has to come from there.
    """
    block = _normalize_agent_loop(
        {
            "profiles": [
                {"id": "team", "preset": "intellect-team", "url": "http://gw:8642"},
                {
                    "id": "community",
                    "preset": "intellect",
                    "transport": "http",
                    "url": "http://gw:8642",
                },
                {"id": "generic", "preset": "custom-http", "url": "http://as:9000"},
                {"id": "override", "preset": "intellect-team", "turn_path": "/elsewhere"},
            ]
        }
    )
    by_id = {profile["id"]: profile for profile in block["profiles"]}

    assert by_id["team"]["turn_path"] == "/v1/runs"
    assert by_id["community"]["turn_path"] == "/v1/runs"
    assert by_id["generic"]["turn_path"] == "/agent/turn"  # the contract's own
    assert by_id["override"]["turn_path"] == "/elsewhere"  # an explicit path wins


def test_tenant_id_survives_normalization_verbatim() -> None:
    """The instance tenant is compared character for character by the service.

    Re-casing or trimming it here would make a deployment that copied a
    lowercase id from its service stop matching, so the only thing this layer
    does is drop surrounding whitespace.
    """
    block = _normalize_agent_loop(
        {
            "profiles": [
                {"id": "a", "preset": "intellect-team", "tenant_id": "  " + "ab" * 16 + "  "},
                {"id": "b", "preset": "intellect-team", "tenant_id": "AB" * 16},
                {"id": "c", "preset": "intellect-team"},
            ]
        }
    )
    by_id = {profile["id"]: profile for profile in block["profiles"]}

    assert by_id["a"]["tenant_id"] == "ab" * 16
    assert by_id["b"]["tenant_id"] == "AB" * 16  # casing preserved
    assert by_id["c"]["tenant_id"] == ""  # absent = the service's own default
