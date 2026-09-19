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
        "transport": False,
        "command": False,
        "url": False,
        "api_key": False,
    }
    # The community preset exposes both ways to reach Intellect, so the picker
    # offers them from one card instead of listing two pseudo-presets.
    intellect = next(preset for preset in data["presets"] if preset["name"] == "intellect")
    assert intellect["default_transport"] == "acp"
    assert {entry["id"] for entry in intellect["transports"]} == {"acp", "http"}
    assert all(entry["detect_key"] for entry in intellect["transports"])
    # The folded preset name must be gone from the catalogue entirely.
    assert "intellect-runs" not in preset_names
    # With nothing configured the shell stub drives turns, so no backend is
    # resolved and the LLM section stays off.
    assert data["effective_primary"] == {
        "id": "",
        "name": "",
        "preset": "",
        "transport": "",
        "family": "",
        "per_turn_model": False,
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
        not r["available"] and r["local"]
        for key, r in http.items()
        if not r["key"].startswith("intellect")
    )
    # Profiles without a URL are not probed at all. The Intellect run channels
    # carry preset-level probes, so the probe count is the agentscope profile
    # plus those two — the team preset gaining a probe target is the fix for a
    # preset that previously had none at all.
    assert len(http) == 3
    # The community runs channel is keyed per transport (`intellect:http`), so
    # each button in the picker can show its own badge.
    assert http["intellect:http"]["local"] is True
    assert http["intellect-team"]["local"] is True


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


def test_identity_mode_rides_the_profile_round_trip(client: TestClient) -> None:
    """The bridge is a per-profile setting, so it must survive save and read
    back — and an unknown value must land on `off`, never on a mode that
    forwards identity."""
    data = _put(
        client,
        [
            _profile(preset="intellect-team", url="https://gw", identity_mode="token"),
            _profile(preset="intellect-team", url="https://gw2", identity_mode="nonsense"),
        ],
    )
    modes = sorted(p["identity_mode"] for p in data["settings"]["profiles"])
    assert modes == ["off", "token"]


def test_a_linked_credential_is_never_echoed_by_the_settings_api(
    client: TestClient,
) -> None:
    """The link belongs to one user and its token is a credential: the API
    reports the link and nothing about the secret."""
    response = client.get("/api/settings/agent-loop/identity")
    assert response.status_code == 200
    payload = response.json()
    assert payload["linked"] is False
    assert payload["stale"] is False
    # Whether the deployment has a service to link against is reported as a
    # bare boolean — the card self-hides on it, and the service URL is
    # admin-owned configuration an ordinary user has no business reading.
    assert isinstance(payload["available"], bool)
    assert "url" not in payload

    # No stored link → nothing to disconnect, and still no error.
    deleted = client.delete("/api/settings/agent-loop/identity")
    assert deleted.status_code == 200

    # A link request with neither a token nor credentials is a client error,
    # not a silent no-op that looks like it worked.
    empty = client.post("/api/settings/agent-loop/identity", json={"token": "", "login_name": ""})
    assert empty.status_code == 400


# ── cross-site requests must not drive a credential change ─────────────────


def test_a_cross_site_link_request_is_refused(client: TestClient) -> None:
    """A forged cross-site POST would let an attacker bind the victim's account
    to *their* Intellect identity, so the victim's turns would run as the
    attacker. CORS does not stop it (the request executes regardless of whether
    the browser is allowed to read the response), so the origin is checked
    directly."""
    response = client.post(
        "/api/settings/agent-loop/identity",
        json={"token": "imt_attacker"},
        headers={"Origin": "https://evil.example"},
    )
    assert response.status_code == 403
    # Nothing was stored, and the token never reached the service.
    assert client.get("/api/settings/agent-loop/identity").json()["linked"] is False


def test_a_cross_site_unlink_is_refused(client: TestClient) -> None:
    response = client.delete(
        "/api/settings/agent-loop/identity",
        headers={"Origin": "https://evil.example"},
    )
    assert response.status_code == 403


def test_an_opaque_origin_is_refused(client: TestClient) -> None:
    """A sandboxed iframe or file:// page sends ``Origin: null`` — the standard
    way to launder a cross-site request."""
    response = client.post(
        "/api/settings/agent-loop/identity",
        json={"token": "imt_attacker"},
        headers={"Origin": "null"},
    )
    assert response.status_code == 403


def test_same_origin_and_non_browser_callers_are_allowed(client: TestClient) -> None:
    """Same-origin is the normal app; no Origin at all is curl/SDK/tests."""
    same = client.post(
        "/api/settings/agent-loop/identity",
        json={"token": ""},
        headers={"Origin": "http://testserver"},
    )
    # Reached the handler (rejected for the empty payload, not for its origin).
    assert same.status_code == 400

    headless = client.delete("/api/settings/agent-loop/identity")
    assert headless.status_code == 200


def test_the_cross_site_guard_also_covers_the_codex_lifecycle(client: TestClient) -> None:
    """Same class of exposure: these establish or tear down a credential."""
    for path in (
        "/api/settings/providers/openai-codex/oauth/start",
        "/api/settings/providers/openai-codex/oauth/logout",
    ):
        response = client.post(path, headers={"Origin": "https://evil.example"})
        assert response.status_code == 403, path


def test_a_malformed_tenant_id_is_refused_while_saving(client: TestClient) -> None:
    """Intellect compares the tenant header with its own configured tenant and
    refuses anything that is not 32 hex characters — on every turn. Catching a
    typo at save time is the difference between one error message and a
    deployment where every turn fails with a 400 from the service."""
    response = client.put(
        "/api/settings/agent-loop",
        json={
            "profiles": [
                _profile(
                    id="team",
                    preset="intellect-team",
                    url="https://gw",
                    tenant_id="not-a-tenant",
                )
            ]
        },
    )
    assert response.status_code == 400
    assert "32 hex" in response.json()["detail"]

    # A well-formed id is accepted and survives the round trip.
    data = _put(
        client,
        [
            _profile(
                id="team",
                preset="intellect-team",
                url="https://gw",
                tenant_id="AB" * 16,
            )
        ],
    )
    (profile,) = data["settings"]["profiles"]
    assert profile["tenant_id"] == "AB" * 16  # casing is the service's business


def test_an_absent_identity_mode_takes_the_preset_default(client: TestClient) -> None:
    """The self-hosted Intellect services attribute turns to the calling
    account by default (the shape the sibling enterprise UI sends); every other
    preset keeps sending nothing. An explicit "off" is honoured, because that is
    how an operator turns attribution back off."""
    data = _put(
        client,
        [
            _profile(preset="intellect-team", url="https://gw"),
            _profile(preset="hermes", url="https://h"),
            _profile(preset="intellect-team", url="https://gw2", identity_mode="off"),
        ],
    )
    modes = {p["url"]: p["identity_mode"] for p in data["settings"]["profiles"]}
    assert modes["https://gw"] == "header"  # absent = the preset default
    assert modes["https://h"] == "off"  # a service that knows no such header
    assert modes["https://gw2"] == "off"  # an explicit choice is honoured


def test_the_profiles_turn_path_takes_the_presets_endpoint(client: TestClient) -> None:
    """The settings page leaves the field blank; both Intellect presets must
    still resolve to the run channel they actually speak, not the generic
    contract path the service never implemented."""
    data = _put(
        client,
        [
            _profile(preset="intellect-team", url="https://gw", turn_path=""),
            _profile(preset="hermes", url="https://h", turn_path=""),
        ],
    )
    paths = sorted(p["turn_path"] for p in data["settings"]["profiles"])
    assert paths == ["/agent/turn", "/v1/runs"]


# ---------------------------------------------------------------------------
# GET /api/settings/agent-loop/models — the composer picker's option source
# ---------------------------------------------------------------------------


def test_models_endpoint_hides_the_picker_without_a_backend(client: TestClient) -> None:
    data = client.get("/api/settings/agent-loop/models").json()
    assert data == {
        "per_turn_model": False,
        "source": "none",
        "backend_label": "",
        "options": [],
    }


def test_models_endpoint_lists_profile_vocabulary_for_cli(
    client: TestClient, settings_dir: Path
) -> None:
    _put(
        client,
        [
            _profile(
                id="cc",
                name="Claude Code CLI",
                preset="claude-code",
                model="opus",
                models=["sonnet", {"id": "ghost", "name": "Ghost"}],
            )
        ],
        primary="cc",
    )
    data = client.get("/api/settings/agent-loop/models").json()

    assert data["per_turn_model"] is True
    assert data["source"] == "profile"
    assert data["backend_label"] == "Claude Code CLI"
    assert data["options"] == [
        {"id": "sonnet", "name": "sonnet", "is_current": False},
        {"id": "ghost", "name": "Ghost", "is_current": False},
        {"id": "opus", "name": "opus", "is_current": True},
    ]


def test_models_endpoint_opts_http_turn_in_via_the_models_list(
    client: TestClient, settings_dir: Path
) -> None:
    """A plain HTTP service: no list → picker hidden; a list → profile source."""
    _put(
        client, [_profile(id="h1", name="Hermes", preset="hermes", url="http://h:1")], primary="h1"
    )
    assert client.get("/api/settings/agent-loop/models").json()["per_turn_model"] is False

    _put(
        client,
        [
            _profile(
                id="h2",
                name="Hermes",
                preset="hermes",
                url="http://h:1",
                models=["qwen-max"],
            )
        ],
        primary="h2",
    )
    data = client.get("/api/settings/agent-loop/models").json()
    assert data["per_turn_model"] is True
    assert data["source"] == "profile"
    assert data["options"] == [{"id": "qwen-max", "name": "qwen-max", "is_current": False}]


def test_models_endpoint_defers_to_the_catalog_for_intellect_http(
    client: TestClient, settings_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The conversation catalog is the intended configuration for the
    self-hosted Intellect HTTP services; when it has options the endpoint says
    so and the frontend fetches /llm-options (grants stay in one place)."""
    _put(
        client,
        [
            _profile(
                id="team",
                name="Intellect Team",
                preset="intellect-team",
                url="http://r:1",
                models=["fallback-model"],
            )
        ],
        primary="team",
    )
    monkeypatch.setattr(
        settings_router,
        "allowed_llm_options",
        lambda: {
            "active": {"profile_id": "p", "model_id": "m"},
            "options": [{"profile_id": "p", "model_id": "m"}],
        },
    )

    data = client.get("/api/settings/agent-loop/models").json()
    assert data["per_turn_model"] is True
    assert data["source"] == "catalog"
    assert data["options"] == []

    # An empty catalog falls back to the profile vocabulary.
    monkeypatch.setattr(
        settings_router, "allowed_llm_options", lambda: {"active": None, "options": []}
    )
    data = client.get("/api/settings/agent-loop/models").json()
    assert data["source"] == "profile"
    assert data["options"] == [
        {"id": "fallback-model", "name": "fallback-model", "is_current": False}
    ]


def test_models_endpoint_acp_reports_real_options_or_profile_fallback(
    client: TestClient, settings_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ACP lists the agent's advertised selector when the probe succeeds and
    falls back to the profile vocabulary when it does not."""
    _put(
        client,
        [
            _profile(
                id="intellect",
                name="Intellect 社区版",
                preset="intellect",
                transport="acp",
                model="deepseek-flash",
                models=["deepseek:deepseek-flash", "deepseek-flash"],
            )
        ],
        primary="intellect",
    )

    class _FakeBackend:
        def __init__(self, options) -> None:
            self._options = options

        async def list_model_options(self, session_id: str = ""):
            return self._options

    import kagweb.services.agent_loop as agent_loop_package

    monkeypatch.setattr(
        agent_loop_package,
        "build_agent_loop_backend",
        lambda profile: _FakeBackend(
            [{"id": "deepseek:deepseek-flash", "name": "deepseek-flash", "is_current": True}]
        ),
    )
    data = client.get("/api/settings/agent-loop/models").json()
    assert data["per_turn_model"] is True
    assert data["source"] == "acp"
    assert data["options"][0]["id"] == "deepseek:deepseek-flash"

    # Probe failure (factory raises / backend None / listing error) → profile list.
    monkeypatch.setattr(agent_loop_package, "build_agent_loop_backend", lambda profile: None)
    data = client.get("/api/settings/agent-loop/models").json()
    assert data["source"] == "profile"
    assert data["options"] == [
        {"id": "deepseek:deepseek-flash", "name": "deepseek:deepseek-flash", "is_current": False},
        {"id": "deepseek-flash", "name": "deepseek-flash", "is_current": True},
    ]


def test_models_endpoint_survives_a_broken_catalog(
    client: TestClient, settings_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A catalog error must degrade to the profile list, never fail the fetch."""
    _put(
        client,
        [
            _profile(
                id="team",
                name="Intellect Team",
                preset="intellect-team",
                url="http://r:1",
                models=["fallback-model"],
            )
        ],
        primary="team",
    )

    def _boom():
        raise RuntimeError("catalog unavailable")

    monkeypatch.setattr(settings_router, "allowed_llm_options", _boom)
    data = client.get("/api/settings/agent-loop/models").json()
    assert data["source"] == "profile"
    assert data["options"] == [
        {"id": "fallback-model", "name": "fallback-model", "is_current": False}
    ]
