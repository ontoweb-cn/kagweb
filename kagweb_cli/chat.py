"""Interactive chat REPL."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import shlex
from typing import Any

from rich.panel import Panel
from rich.text import Text
import typer

from kagweb.app import KAGWebApp, TurnRequest

from .common import (
    console,
    maybe_run,
    parse_config_items,
    parse_json_object,
    read_console_input,
    regenerate_and_render,
    render_tool_result_entry,
    run_turn_and_render,
    tool_results,
)


@dataclass
class ChatState:
    session_id: str | None = None
    capability: str = "chat"
    tools: list[str] = field(default_factory=list)
    language: str = "en"
    history_references: list[str] = field(default_factory=list)
    config: dict[str, Any] = field(default_factory=dict)


def register(app: typer.Typer) -> None:
    @app.callback(invoke_without_command=True)
    def chat(
        ctx: typer.Context,
        session: str | None = typer.Option(None, "--session", help="Resume an existing session."),
        tool: list[str] = typer.Option([], "--tool", "-t", help="Pre-enable tool(s)."),
        capability: str = typer.Option("chat", "--capability", "-c", help="Initial capability."),
        history_ref: list[str] = typer.Option([], "--history-ref", help="Referenced session ids."),
        language: str = typer.Option("en", "--language", "-l", help="Response language."),
        config: list[str] = typer.Option([], "--config", help="Initial config key=value."),
        config_json: str | None = typer.Option(
            None, "--config-json", help="Initial config as JSON."
        ),
    ) -> None:
        """Enter interactive chat REPL. Use `kagweb run` for single-turn execution."""
        if ctx.invoked_subcommand is not None:
            return

        try:
            initial_config = parse_json_object(config_json)
            initial_config.update(parse_config_items(config))
        except ValueError as exc:
            raise typer.BadParameter(str(exc)) from exc

        state = ChatState(
            session_id=session,
            capability=capability,
            tools=list(tool),
            language=language,
            history_references=[item.strip() for item in history_ref if item.strip()],
            config=initial_config,
        )
        maybe_run(_chat_repl(state))


async def _chat_repl(state: ChatState) -> None:
    client = KAGWebApp()

    if state.session_id:
        existing = await client.get_session(state.session_id)
        if existing is None:
            console.print(f"[red]Session not found:[/] {state.session_id}")
            raise typer.Exit(code=1)
        preferences = existing.get("preferences", {}) or {}
        state.capability = str(preferences.get("capability") or state.capability or "chat")
        state.tools = list(preferences.get("tools") or state.tools)
        state.language = str(preferences.get("language") or state.language)
        state.history_references = list(
            preferences.get("history_references") or state.history_references
        )

    console.print(
        Panel(
            "[bold]KAGWeb CLI[/]\n"
            "Type a message to chat. Ctrl-C interrupts a running turn. Commands:\n"
            "  /quit  /session  /status  /new  /clear\n"
            "  /regenerate (alias /retry) — re-run the last user message\n"
            "  /tool on|off <name>\n"
            "  /cap <name>\n"
            "  /history add <id> | /history clear\n"
            "  /show last|<n> — expand a tool result or captured thinking\n"
            "  /refs  /config show|set|clear",
            title="kagweb chat",
        )
    )
    _print_state(state)

    try:
        while True:
            try:
                user_input = _read_repl_input()
            except (EOFError, KeyboardInterrupt):
                console.print()
                break
            except UnicodeDecodeError:
                console.print(
                    "[yellow]Unable to decode terminal input. "
                    "Check the terminal encoding and try again.[/]"
                )
                continue

            if not user_input:
                continue
            if user_input.startswith("/"):
                command = user_input.split(maxsplit=1)[0].lower()
                if command in {"/regenerate", "/retry"}:
                    if not state.session_id:
                        console.print("[yellow]No active session yet — send a message first.[/]")
                        continue
                    result = await regenerate_and_render(
                        app=client,
                        session_id=state.session_id,
                        capability=state.capability,
                        fmt="rich",
                    )
                    if result is not None:
                        session, _turn = result
                        state.session_id = str(session["id"])
                    continue
                should_continue = _apply_command(user_input, state)
                if should_continue:
                    continue
                break

            request = TurnRequest(
                content=user_input,
                capability=state.capability,
                session_id=state.session_id,
                tools=list(state.tools),
                language=state.language,
                config=dict(state.config),
                history_references=list(state.history_references),
            )
            session, _turn = await run_turn_and_render(app=client, request=request, fmt="rich")
            state.session_id = str(session["id"])
    finally:
        pass


def _apply_command(raw: str, state: ChatState) -> bool:
    try:
        parts = shlex.split(raw)
    except ValueError as exc:
        console.print(f"[yellow]Could not parse command:[/] {exc}")
        return True
    if not parts:
        return True
    command = parts[0].lower()
    if command == "/quit":
        return False
    if command == "/session":
        console.print(f"session={state.session_id or '(new)'}")
        return True
    if command == "/status":
        _print_state(state)
        return True
    if command in {"/new", "/clear"}:
        state.session_id = None
        console.print("[dim]Started a new chat context.[/]")
        return True
    if command == "/refs":
        _print_refs(state)
        return True
    if command == "/tool" and len(parts) >= 3:
        action, tool_name = parts[1], parts[2]
        if action == "on" and tool_name not in state.tools:
            state.tools.append(tool_name)
        elif action == "off" and tool_name in state.tools:
            state.tools.remove(tool_name)
        _print_state(state)
        return True
    if command == "/cap" and len(parts) >= 2:
        state.capability = parts[1]
        _print_state(state)
        return True
    if command == "/history" and len(parts) >= 2:
        if parts[1] == "clear":
            state.history_references = []
        elif parts[1] == "add" and len(parts) >= 3:
            state.history_references.append(parts[2])
        _print_state(state)
        return True
    if command == "/show":
        selector = parts[1] if len(parts) >= 2 else "last"
        entry = tool_results.get(selector)
        if entry is None:
            if selector == "last":
                console.print("[dim]No tool result captured yet in this session.[/]")
            else:
                console.print(
                    f"[dim]No tool result matches [bold]{selector}[/]. "
                    f"Available: {[e.index for e in tool_results.entries()] or 'none'}.[/]"
                )
        else:
            render_tool_result_entry(entry)
        return True
    if command == "/config" and len(parts) >= 2:
        subcommand = parts[1]
        if subcommand == "show":
            console.print_json(_format_config(state.config))
        elif subcommand == "clear":
            state.config = {}
        elif subcommand == "set":
            parsed = _parse_config_assignment(parts)
            if parsed is None:
                console.print("[yellow]Usage:[/] /config set key=value or /config set key value")
                return True
            key, value = parsed
            state.config[key] = _parse_config_value(value)
        _print_state(state)
        return True

    console.print("[dim]Unknown command.[/]")
    return True


def _read_repl_input() -> str:
    """Read one REPL message, supporting backslash-continued multi-line input."""

    lines: list[str] = []
    prompt = "[bold green]You>[/] "
    while True:
        line = read_console_input(prompt)
        if line.endswith("\\"):
            lines.append(line[:-1])
            prompt = "[dim]...[/] "
            continue
        lines.append(line)
        return "\n".join(lines).strip()


def _print_state(state: ChatState) -> None:
    _print_literal(
        f"session={state.session_id or '(new)'} "
        f"capability={state.capability} "
        f"tools={_format_list(state.tools)} "
        f"history={_format_list(state.history_references)} "
        f"language={state.language} "
        f"config={_format_config(state.config)}",
        style="dim",
    )


def _print_refs(state: ChatState) -> None:
    _print_literal("Current state:", style="bold")
    fields = (
        ("session", state.session_id or "(new)"),
        ("capability", state.capability),
        ("tools", _format_list(state.tools)),
        ("history", _format_list(state.history_references)),
        ("language", state.language),
        ("config", _format_config(state.config)),
    )
    for label, value in fields:
        _print_literal(f"  {label:<12}{value}")


def _print_literal(value: str, *, style: str = "") -> None:
    """Print dynamic CLI text without interpreting it as Rich markup."""

    console.print(Text(value, style=style), highlight=False)


def _format_list(items: list[str]) -> str:
    return "[" + ", ".join(items) + "]" if items else "[]"


def _format_config(config: dict[str, Any]) -> str:
    return json.dumps(config, ensure_ascii=False, sort_keys=True)


def _parse_config_assignment(parts: list[str]) -> tuple[str, str] | None:
    if len(parts) >= 3 and "=" in parts[2]:
        key, _, value = parts[2].partition("=")
        key = key.strip()
        return (key, value) if key else None
    if len(parts) >= 4:
        key = parts[2].strip()
        value = " ".join(parts[3:]).strip()
        return (key, value) if key and value else None
    return None


def _parse_config_value(raw_value: str) -> Any:
    try:
        return json.loads(raw_value)
    except (json.JSONDecodeError, TypeError):
        lowered = raw_value.lower()
        if lowered == "true":
            return True
        if lowered == "false":
            return False
        if lowered in {"null", "none"}:
            return None
        return raw_value
