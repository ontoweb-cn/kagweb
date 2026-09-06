"""CLI config command tests."""

from __future__ import annotations

from types import SimpleNamespace

from typer.testing import CliRunner

from kagweb_cli.main import app

runner = CliRunner()


def test_config_show_reports_unconfigured_services(monkeypatch) -> None:
    """CLI-only defaults leave some services unconfigured; config show must not traceback."""
    import kagweb.services.config as config

    monkeypatch.setattr(
        config,
        "load_system_settings",
        lambda: {"backend_port": 8082, "frontend_port": 8092},
    )
    monkeypatch.setattr(
        config,
        "resolve_llm_runtime_config",
        lambda: SimpleNamespace(
            binding_hint="openai",
            provider_name="openai",
            provider_mode="standard",
            model="gpt-4o-mini",
            effective_url="https://api.openai.com/v1",
            api_version=None,
            extra_headers={},
            api_key="",
        ),
    )

    monkeypatch.setattr(
        config,
        "resolve_search_runtime_config",
        lambda: SimpleNamespace(
            provider="duckduckgo",
            requested_provider="duckduckgo",
            status="ok",
            fallback_reason=None,
            base_url="",
            proxy=None,
            api_key="",
        ),
    )
    monkeypatch.setattr(
        config,
        "load_config_with_main",
        lambda _name: {"system": {"language": "en"}, "tools": {}},
    )

    result = runner.invoke(app, ["config", "show"])

    assert result.exit_code == 0, result.output
