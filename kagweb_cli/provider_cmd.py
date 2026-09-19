"""CLI commands for provider auth and access validation."""

from __future__ import annotations

import webbrowser

import typer

from .common import maybe_run


def register(app: typer.Typer) -> None:
    @app.command("login")
    def provider_login(
        provider: str = typer.Argument(
            ...,
            help=(
                "Provider: github-copilot (validate existing Copilot auth) | "
                "codebuddy (validate CodeBuddy SDK auth)"
            ),
        ),
    ) -> None:
        """Authenticate or validate provider access."""
        key = provider.strip().lower().replace("-", "_")
        if key == "github_copilot":
            maybe_run(_login_github_copilot())
            return
        if key in {"codebuddy", "codebuddy_code", "workbuddy"}:
            maybe_run(_login_codebuddy())
            return
        raise typer.BadParameter(
            f"Unknown provider `{provider}`. Supported: github-copilot, codebuddy"
        )


async def _login_github_copilot() -> None:
    """Validate an existing GitHub Copilot auth session via a lightweight request."""
    try:
        from openai import AsyncOpenAI
    except ImportError:
        typer.echo(
            "openai is not installed. Install CLI deps from a local checkout: "
            "python -m pip install -e ./packaging/kagweb-cli"
        )
        raise typer.Exit(code=1)
    try:
        client = AsyncOpenAI(
            api_key="copilot",
            base_url="https://api.githubcopilot.com",
            max_retries=0,
        )
        await client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=1,
        )
    except Exception as exc:
        typer.echo(f"GitHub Copilot auth validation failed: {exc}")
        raise typer.Exit(code=1) from exc
    typer.echo("GitHub Copilot auth validation succeeded.")


async def _login_codebuddy() -> None:
    """Validate CodeBuddy auth, starting an SDK login when needed."""
    from kagweb.services.codebuddy_credentials import load_credentials, probe_account

    credentials = load_credentials()
    if credentials is not None:
        label = await probe_account(credentials)
        if label:
            typer.echo(f"CodeBuddy auth validation succeeded for {label}.")
            return

    try:
        from codebuddy_agent_sdk import authenticate, query
    except ImportError:
        typer.echo(
            "CodeBuddy is not signed in. Sign in with the CodeBuddy IDE plugin, run "
            "`codebuddy` and enter `/login`, or set CODEBUDDY_API_KEY. For in-app login "
            "install the SDK: `python -m pip install codebuddy-agent-sdk`."
        )
        raise typer.Exit(code=1)

    try:
        saw_message = False
        async for _message in query(prompt="Reply with only OK."):
            saw_message = True
        if not saw_message:
            raise RuntimeError("CodeBuddy SDK returned no messages.")
    except Exception as exc:
        if "Authentication required" not in str(exc):
            typer.echo(f"CodeBuddy auth validation failed: {exc}")
            raise typer.Exit(code=1) from exc
        typer.echo("CodeBuddy is not logged in. Starting CodeBuddy login flow ...")
        try:
            auth = await authenticate(timeout=300.0)
            if getattr(auth, "auth_url", ""):
                typer.echo(f"Open this URL to sign in: {auth.auth_url}")
                try:
                    webbrowser.open(auth.auth_url)
                except Exception:
                    pass
            result = await auth
        except Exception as login_exc:
            typer.echo(f"CodeBuddy login failed: {login_exc}")
            typer.echo("You can also run `codebuddy`, enter `/login`, or set CODEBUDDY_API_KEY.")
            raise typer.Exit(code=1) from login_exc
        userinfo = getattr(result, "userinfo", None)
        label = (
            getattr(userinfo, "user_nickname", None)
            or getattr(userinfo, "user_name", None)
            or getattr(userinfo, "user_id", None)
            or "current account"
        )
        typer.echo(f"CodeBuddy auth validation succeeded for {label}.")
        return
    typer.echo("CodeBuddy auth validation succeeded.")
