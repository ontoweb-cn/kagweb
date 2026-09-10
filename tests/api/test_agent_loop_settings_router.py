"""The /api/settings/agent-loop endpoints: profiles v2, redaction, tri-state
keys, auto-primary, detection, and the no-live-turn configuration check."""

from __future__ import annotations

import json
from pathlib import Path
import sys
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

from kagweb.api import main as api_main
from kagweb.api.routers import settings as settings_router
from kagweb.services.config.runtime_settings import RuntimeSettingsService


@pytest.fixture()
def settings_dir(tmp_path: Path) -> Path:
    # Shared with `client` below: pytest hands the same tmp_path instance to
    # every fixture of one test, so reads here see what the client wrote.
    return tmp_path


@pytest.fixture()
def client(settings_dir: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Admin client over an isolated settings directory."""

    def _service() -> RuntimeSettingsService:
        return RuntimeSettingsService(settings_dir, process_env={})

    monkeypatch.setattr(settings_router, "get_runtime_settings_service", _service)
    monkeypatch.setattr(settings_router, "get_current_user", lambda: SimpleNamespace(is_admin=True))
    return TestClient(api_main.app)


def _profile(**overrides: object) -> dict:
    payload: dict = {
        "id": "",
        "name": "",
        "preset": "",
        "enabled": True,
        "command": "",
        "args": [],
        "env": {},
        "url": "",
        "turn_path": "",
        "headers": {},
        "api_key": None,
        "timeout_seconds": 900,
        "session_workspace": True,
        "consult_enabled": True,
    }
    payload.update(overrides)
    return payload


def _put(client: TestClient, profiles: list[dict], **extra: object) -> dict:
    response = client.put(
        "/api/settings/agent-loop",
        json={"profiles": profiles, **extra},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_get_returns_presets_and_bounds(client: TestClient) -> None:
    response = client.get("/api/settings/agent-loop")
    assert response.status_code == 200
    data = response.json()

    preset_names = {preset["name"] for preset in data["presets"]}
    assert {"intellect", "intellect-team", "hermes", "agentscope"} <= preset_names
    assert {"claude-code", "codex", "opencode", "custom-cli", "custom-http"} <= preset_names
    assert data["settings"]["profiles"] == []
    assert data["settings"]["primary"] == ""
    assert data["bounds"]["timeout_seconds"] == [30, 86400]
    assert data["bounds"]["consult_budget"] == [0, 12]
    assert data["env_overrides"] == {
        "preset": False,
        "command": False,
        "url": False,
        "api_key": False,
    }
    # With nothing configured the shell stub drives turns, so no backend is
    # resolved and the LLM section stays off.
    assert data["effective_primary"] == {
        "id": "",
        "name": "",
        "preset": "",
        "family": "",
        "llm_settings_enabled": False,
    }


def test_effective_primary_gates_llm_settings_by_family(
    client: TestClient, settings_dir: Path
) -> None:
    """Only a self-hosted HTTP backend gets the LLM settings section.

    The CLI family carries its own login state, so presenting model credentials
    for it is the same misleading affordance the section is meant to remove.
    """
    # Intellect enterprise is a self-hosted HTTP service → LLM settings apply.
    data = _put(
        client,
        [_profile(id="team", preset="intellect-team", url="http://intellect.test")],
        primary="team",
    )
    payload = client.get("/api/settings/agent-loop").json()
    assert payload["effective_primary"]["preset"] == "intellect-team"
    assert payload["effective_primary"]["family"] == "http"
    assert payload["effective_primary"]["llm_settings_enabled"] is True
    assert data  # the PUT above is the setup, not the subject

    # Intellect community is now a CLI/ACP backend → it does not.
    _put(
        client,
        [_profile(id="acp", preset="intellect", command="intellect")],
        primary="acp",
    )
    payload = client.get("/api/settings/agent-loop").json()
    assert payload["effective_primary"]["preset"] == "intellect"
    assert payload["effective_primary"]["family"] == "cli"
    assert payload["effective_primary"]["llm_settings_enabled"] is False

    # A generic HTTP service configures its own models → it does not either.
    _put(
        client,
        [_profile(id="hermes", preset="hermes", url="http://hermes.test")],
        primary="hermes",
    )
    payload = client.get("/api/settings/agent-loop").json()
    assert payload["effective_primary"]["llm_settings_enabled"] is False


def test_put_saves_profiles_and_auto_primary_prefers_local_intellect(
    client: TestClient, settings_dir: Path
) -> None:
    data = _put(
        client,
        [
            _profile(preset="hermes", name="远端 HERMES", url="https://hermes.example"),
            _profile(preset="intellect-team", name="本地 Intellect", url="http://localhost:8083"),
        ],
        # primary omitted (None) — the default rule must pick local Intellect.
    )

    profiles = {p["name"]: p for p in data["settings"]["profiles"]}
    assert data["settings"]["primary"] == profiles["本地 Intellect"]["id"]
    assert data["settings"]["consult_budget"] == 3
    # New profiles got server-assigned ids.
    assert all(profile["id"] for profile in data["settings"]["profiles"])
    # api_key never echoes; api_key_set rides along per profile.
    assert all("api_key" not in profile for profile in data["settings"]["profiles"])
    assert all(profile["api_key_set"] is False for profile in data["settings"]["profiles"])

    saved = json.loads((settings_dir / "system.json").read_text(encoding="utf-8"))
    assert saved["agent_loop"]["version"] == 2
    assert len(saved["agent_loop"]["profiles"]) == 2


def test_put_explicit_primary_and_stub_choice(client: TestClient) -> None:
    data = _put(
        client,
        [
            _profile(id="a", preset="hermes", url="https://h"),
            _profile(id="b", preset="intellect-team", url="http://localhost:1"),
        ],
        primary="a",
    )
    assert data["settings"]["primary"] == "a"

    stub = _put(
        client,
        [_profile(id="a", preset="hermes", url="https://h")],
        primary="",
    )
    assert stub["settings"]["primary"] == ""


def test_put_api_key_tri_state_per_profile(client: TestClient) -> None:
    data = _put(
        client,
        [_profile(preset="hermes", url="https://h", api_key="sk-first")],
    )
    (profile,) = data["settings"]["profiles"]
    assert profile["api_key_set"] is True

    # None keeps the stored credential (matched by the server-assigned id).
    data = _put(
        client,
        [{**profile, "api_key_set": None, "api_key": None}],
    )
    assert data["settings"]["profiles"][0]["api_key_set"] is True

    # "" clears it.
    data = _put(
        client,
        [{**data["settings"]["profiles"][0], "api_key_set": None, "api_key": ""}],
    )
    assert data["settings"]["profiles"][0]["api_key_set"] is False


def test_put_rejects_out_of_range_values(client: TestClient) -> None:
    assert (
        client.put(
            "/api/settings/agent-loop",
            json={"profiles": [], "consult_budget": 99},
        ).status_code
        == 422
    )
    assert (
        client.put(
            "/api/settings/agent-loop",
            json={"profiles": [_profile(preset="hermes", timeout_seconds=5)]},
        ).status_code
        == 422
    )


def test_detect_reports_cli_presets_and_http_profiles(client: TestClient) -> None:
    _put(
        client,
        [
            _profile(preset="agentscope", url="http://127.0.0.1:59999"),
            _profile(preset="hermes", url=""),  # no URL → not probed
        ],
    )
    response = client.get("/api/settings/agent-loop/detect")
    assert response.status_code == 200
    results = response.json()["results"]

    cli = {result["key"]: result for result in results if result["family"] == "cli"}
    assert {"claude-code", "codex", "opencode"} <= set(cli)
    for result in cli.values():
        assert result["local"] is True
        assert isinstance(result["available"], bool)
        assert result["detail"]

    http = {r["key"]: r for r in results if r["family"] == "http"}
    # The unreachable loopback profile is probed and reported unavailable.
    assert any(
        not r["available"] and r["local"] for key, r in http.items() if key != "intellect-runs"
    )
    # Profiles without a URL are not probed at all.
    assert len(http) == 2  # the agentscope profile + the intellect-runs preset probe
    assert http["intellect-runs"]["local"] is True  # preset-level local gateway probe


def test_test_endpoint_reports_stub_and_unknown_backend(client: TestClient) -> None:
    ok = client.post("/api/settings/agent-loop/test", json=_profile(preset="")).json()
    assert ok["ok"] is True
    assert "stub" in ok["message"]

    unknown = client.post(
        "/api/settings/agent-loop/test", json=_profile(preset="no-such-preset")
    ).json()
    assert unknown["ok"] is False


def test_test_endpoint_http_url_checks(client: TestClient) -> None:
    missing = client.post(
        "/api/settings/agent-loop/test", json=_profile(preset="agentscope", url="")
    ).json()
    assert missing["ok"] is False

    bad_scheme = client.post(
        "/api/settings/agent-loop/test",
        json=_profile(preset="agentscope", url="ftp://agentscope:9000"),
    ).json()
    assert bad_scheme["ok"] is False

    good = client.post(
        "/api/settings/agent-loop/test",
        json=_profile(preset="agentscope", url="http://agentscope:9000"),
    ).json()
    assert good["ok"] is True
    assert "POST http://agentscope:9000/agent/turn" in good["message"]


def test_test_endpoint_cli_path_probe(client: TestClient) -> None:
    found = client.post(
        "/api/settings/agent-loop/test",
        json=_profile(preset="custom-cli", command=sys.executable),
    ).json()
    assert found["ok"] is True

    missing = client.post(
        "/api/settings/agent-loop/test",
        json=_profile(preset="custom-cli", command="definitely-not-a-real-cli-xyz"),
    ).json()
    assert missing["ok"] is False
    assert "PATH" in missing["message"]


def test_workdir_inside_allowed_roots_is_persisted(client: TestClient) -> None:
    body = _put(
        client,
        [_profile(id="p1", name="p1", preset="claude-code", workdir="data/user/projects/app")],
        allowed_workdir_roots=["data/user"],
    )
    assert body["settings"]["profiles"][0]["workdir"] == "data/user/projects/app"
    assert body["settings"]["allowed_workdir_roots"] == ["data/user"]


def test_disabled_or_http_profile_workdir_does_not_block_a_save(client: TestClient) -> None:
    """Only enabled CLI profiles can spawn, so a stale workdir on a profile
    that never runs must not wedge unrelated settings changes."""
    body = _put(
        client,
        [
            _profile(
                id="off",
                name="off",
                preset="claude-code",
                enabled=False,
                workdir="../outside",
            ),
            _profile(
                id="http", name="http", preset="hermes", url="https://h", workdir="../outside"
            ),
        ],
        allowed_workdir_roots=["data/user"],
    )
    assert body["settings"]["profiles"][0]["workdir"] == "../outside"


def test_empty_workdir_roots_forbid_every_profile_workdir(client: TestClient) -> None:
    """Clearing the list is the operator's "no workdirs" switch; it must not be
    silently replaced by the default."""
    body = _put(client, [], allowed_workdir_roots=[])
    assert body["settings"]["allowed_workdir_roots"] == []

    response = client.put(
        "/api/settings/agent-loop",
        json={
            "profiles": [
                _profile(id="p1", name="p1", preset="claude-code", workdir="data/user/app")
            ],
            "allowed_workdir_roots": [],
        },
    )
    assert response.status_code == 400
    assert "none configured" in response.json()["detail"]


def test_workdir_outside_allowed_roots_is_rejected(client: TestClient) -> None:
    """The loop runs with the server's privileges, so a save that points it
    anywhere else must fail loudly rather than land in the trace later."""
    response = client.put(
        "/api/settings/agent-loop",
        json={
            "profiles": [_profile(id="p1", name="p1", preset="claude-code", workdir="../outside")],
            "allowed_workdir_roots": ["data/user"],
        },
    )
    assert response.status_code == 400
    assert "outside the allowed roots" in response.json()["detail"]
