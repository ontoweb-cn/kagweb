"""CLI entry point for the standalone ``kagweb-cli`` package."""

from __future__ import annotations

from pathlib import Path

import typer

from kagweb.logging import configure_logging
from kagweb.runtime.mode import RunMode, set_mode

from .chat import register as register_chat
from .common import build_turn_request, console, maybe_run
from .config_cmd import register as register_config
from .doctor import register as register_doctor
from .init_cmd import register as register_init
from .partner import register as register_partner
from .plugin import register as register_plugin
from .provider_cmd import register as register_provider
from .session_cmd import register as register_session

set_mode(RunMode.CLI)
configure_logging()

app = typer.Typer(
    name="kagweb",
    help="KAGWeb CLI – agent-first interface for capabilities, tools, and knowledge.",
    no_args_is_help=True,
    add_completion=False,
)

partner_app = typer.Typer(help="Manage partners (IM-connected companions).")
chat_app = typer.Typer(help="Interactive chat REPL.")
plugin_app = typer.Typer(help="List plugins.")
config_app = typer.Typer(help="Inspect configuration.")
session_app = typer.Typer(help="Manage shared sessions.")
provider_app = typer.Typer(help="Manage provider OAuth login.")

app.add_typer(partner_app, name="partner")
app.add_typer(chat_app, name="chat")
app.add_typer(plugin_app, name="plugin")
app.add_typer(config_app, name="config")
app.add_typer(session_app, name="session")
app.add_typer(provider_app, name="provider")

register_partner(partner_app)
register_chat(chat_app)
register_plugin(plugin_app)
register_config(config_app)
register_session(session_app)
register_provider(provider_app)
register_doctor(app)
register_init(app)


@app.command("run")
def run_capability(
    capability: str = typer.Argument(
        ...,
        help=("Capability name (currently: chat)."),
    ),
    message: str = typer.Argument(..., help="Message to send."),
    session: str | None = typer.Option(None, "--session", help="Existing session id."),
    tool: list[str] = typer.Option([], "--tool", "-t", help="Enabled tool(s)."),
    history_ref: list[str] = typer.Option([], "--history-ref", help="Referenced session ids."),
    language: str = typer.Option("en", "--language", "-l", help="Response language."),
    config: list[str] = typer.Option([], "--config", help="Capability config key=value."),
    config_json: str | None = typer.Option(
        None, "--config-json", help="Capability config as JSON."
    ),
    fmt: str = typer.Option("rich", "--format", "-f", help="Output format: rich | json."),
) -> None:
    """Run any capability in a single turn (agent-first entry point)."""
    from kagweb.app import KAGWebApp

    from .common import run_turn_and_render

    request = build_turn_request(
        content=message,
        capability=capability,
        session_id=session,
        tools=tool,
        language=language,
        config_items=config,
        config_json=config_json,
        history_refs=history_ref,
    )
    maybe_run(run_turn_and_render(app=KAGWebApp(), request=request, fmt=fmt))


@app.command()
def start(
    home: Path | None = typer.Option(None, "--home", help="Runtime workspace root."),
    dev: bool = typer.Option(
        False,
        "--dev",
        help="Use the Next.js development server for frontend work.",
    ),
    detach: bool = typer.Option(
        False,
        "--detach",
        help="Run outside the current console; stop later with `kagweb stop`.",
    ),
    open_browser: bool = typer.Option(
        True,
        "--open-browser/--no-browser",
        help="Open the frontend automatically after startup.",
    ),
) -> None:
    """Launch backend + frontend together. Source installs default to production."""
    from kagweb.runtime.launcher import start as start_web

    start_web(home=home, dev=dev, detach=detach, open_browser=open_browser)


@app.command()
def stop(
    home: Path | None = typer.Option(None, "--home", help="Runtime workspace root."),
) -> None:
    """Stop a KAGWeb launcher started with ``--detach``."""
    from kagweb.runtime.launcher import stop as stop_web

    if not stop_web(home=home):
        raise typer.Exit(code=1)


@app.command()
def serve(
    host: str = typer.Option("0.0.0.0", help="Bind address."),
    port: int | None = typer.Option(None, help="Port number."),
    reload: bool = typer.Option(False, help="Enable auto-reload for development."),
) -> None:
    """Start the KAGWeb API server."""
    import asyncio
    import sys

    set_mode(RunMode.SERVER)
    if port is None:
        from kagweb.services.setup import get_backend_port

        port = get_backend_port()

    # Windows: uvicorn defaults to SelectorEventLoop which does not support
    # asyncio.create_subprocess_exec.  Switch to ProactorEventLoop so that
    # child-process APIs (used by Math Animator renderer, etc.) work correctly.
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

    try:
        import uvicorn
    except ImportError:
        console.print(
            "[bold red]Error:[/] API server dependencies not installed.\nRun: pip install -U kagweb"
        )
        raise typer.Exit(code=1)

    from kagweb.services.config import (
        HTTP_KEEP_ALIVE_TIMEOUT,
        get_ws_max_size,
        load_system_settings,
    )

    backend_workers = max(1, int(load_system_settings().get("backend_workers") or 1))
    if reload and backend_workers > 1:
        console.print("[bold red]Error:[/] --reload cannot be used when backend_workers > 1.")
        raise typer.Exit(code=2)

    # ws_max_size tracks the configured chat-attachment total so base64
    # uploads fit in one WS frame (uvicorn defaults to 16MB).
    uvicorn.run(
        "kagweb.api.main:app",
        host=host,
        port=port,
        reload=reload,
        workers=backend_workers,
        reload_excludes=["web/*", "data/*"] if reload else None,
        ws_max_size=get_ws_max_size(),
        timeout_keep_alive=HTTP_KEEP_ALIVE_TIMEOUT,
    )


def main() -> None:
    app()


if __name__ == "__main__":
    main()
