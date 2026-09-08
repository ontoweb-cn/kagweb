"""CLI commands for shared session management."""

from __future__ import annotations

import json
from pathlib import Path

import typer

from kagweb.app import KAGWebApp
from kagweb.services.session.dsl_export import build_session_dsl, dsl_to_mermaid

from .chat import ChatState, _chat_repl
from .common import console, escape, maybe_run, print_session_table


def register(app: typer.Typer) -> None:
    @app.command("list")
    def list_sessions(
        limit: int = typer.Option(20, "--limit", help="Maximum sessions to show."),
    ) -> None:
        """List existing sessions."""
        maybe_run(_list_sessions(limit))

    @app.command("show")
    def show_session(
        session_id: str = typer.Argument(..., help="Session id."),
        fmt: str = typer.Option("rich", "--format", help="Output format: rich | json."),
    ) -> None:
        """Show a session and its persisted messages."""
        maybe_run(_show_session(session_id, fmt))

    @app.command("open")
    def open_session(
        session_id: str = typer.Argument(..., help="Session id."),
    ) -> None:
        """Enter the interactive chat REPL with an existing session."""
        maybe_run(_chat_repl(ChatState(session_id=session_id)))

    @app.command("delete")
    def delete_session(
        session_id: str = typer.Argument(..., help="Session id."),
    ) -> None:
        """Delete a session and all of its turns/messages."""
        maybe_run(_delete_session(session_id))

    @app.command("rename")
    def rename_session(
        session_id: str = typer.Argument(..., help="Session id."),
        title: str = typer.Option(..., "--title", help="New session title."),
    ) -> None:
        """Rename a session."""
        maybe_run(_rename_session(session_id, title))

    @app.command("trace")
    def session_trace(
        session_id: str = typer.Argument(..., help="Session id."),
        fmt: str = typer.Option("dsl", "--format", help="Output format: dsl | mermaid."),
        stable: bool = typer.Option(
            False, "--stable", help="Strip volatile fields (diff/snapshot friendly)."
        ),
        normalize_ids: bool = typer.Option(
            False, "--normalize-ids", help="Renumber node ids positionally (turn:N)."
        ),
        include_text: bool = typer.Option(
            True, "--text/--no-text", help="Include message text previews."
        ),
        out: Path = typer.Option(
            None, "--out", help="Write the export to a file instead of stdout."
        ),
    ) -> None:
        """Export a session's reasoning chain as Session DSL JSON or Mermaid."""
        maybe_run(_session_trace(session_id, fmt, stable, normalize_ids, include_text, out))

    @app.command("diff")
    def session_diff(
        a: Path = typer.Argument(..., help="First DSL export (.json)."),
        b: Path = typer.Argument(..., help="Second DSL export (.json)."),
        as_json: bool = typer.Option(False, "--json", help="Emit a machine-readable JSON summary."),
    ) -> None:
        """Structurally diff two Session DSL exports.

        Trace entries pair by position: a mid-sequence insert or delete
        shifts the alignment of everything after it. Export both sides
        with --stable --normalize-ids for meaningful comparisons.
        """
        _session_diff(a, b, as_json)


async def _list_sessions(limit: int) -> None:
    client = KAGWebApp()
    sessions = await client.list_sessions(limit=limit)
    print_session_table(sessions)


async def _session_trace(
    session_id: str,
    fmt: str,
    stable: bool,
    normalize_ids: bool,
    include_text: bool,
    out: Path | None,
) -> None:
    if fmt not in ("dsl", "mermaid"):
        console.print(f"[red]Unsupported format:[/] {fmt} (supported: dsl, mermaid)")
        raise typer.Exit(code=1)
    client = KAGWebApp()
    session = await client.get_session(session_id)
    if session is None:
        console.print(f"[red]Session not found:[/] {session_id}")
        raise typer.Exit(code=1)
    doc = build_session_dsl(
        session.get("messages", []),
        stable=stable,
        normalize_ids=normalize_ids,
        include_text=include_text,
        session_id=session_id,
    )
    if fmt == "mermaid":
        text = dsl_to_mermaid(doc)
    else:
        text = json.dumps(doc, ensure_ascii=False, indent=2)
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
        console.print(f"[green]Exported[/] {session_id} -> {out}")
        return
    if fmt == "mermaid":
        # markup=False: rich would otherwise parse [/...] sequences in user
        # text as closing tags and raise MarkupError (#75); soft_wrap keeps
        # the byte-level parity with the web export (#66).
        console.print(text, highlight=False, soft_wrap=True, markup=False)
    else:
        # Same for the JSON dump — message content may contain rich-style tags.
        console.print(text, highlight=False, markup=False)


def _load_dsl_document(path: Path) -> dict:
    if not path.is_file():
        console.print(f"[red]File not found:[/] {path}")
        raise typer.Exit(code=1)
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        console.print(f"[red]Cannot read {path}:[/] {exc}")
        raise typer.Exit(code=1) from exc
    if not isinstance(doc, dict) or not isinstance(doc.get("trace"), list):
        console.print(
            f"[red]Not a Session DSL export:[/] {path} (expected a JSON object with a `trace` list)"
        )
        raise typer.Exit(code=1)
    return doc


def _session_diff(a: Path, b: Path, as_json: bool) -> None:
    from rich.table import Table

    from kagweb.services.session.dsl_diff import diff_session_dsl, is_empty

    doc_a = _load_dsl_document(a)
    doc_b = _load_dsl_document(b)
    result = diff_session_dsl(doc_a, doc_b)

    if as_json:
        # markup=False: user text inside the diff payload may contain
        # rich-style closing tags that would raise MarkupError (#75).
        console.print(
            json.dumps(result, ensure_ascii=False, indent=2),
            highlight=False,
            soft_wrap=True,
            markup=False,
        )
        return

    if is_empty(result):
        console.print(f"[green]No differences[/] — {result['a_turns']} turns compared.")
        return

    table = Table(title="Session DSL diff")
    table.add_column("Position", justify="right")
    table.add_column("Result")
    table.add_column("Detail")
    for item in result["modified"]:
        detail = "; ".join(_describe_change(change) for change in item["changes"])
        table.add_row(str(item["position"]), "[yellow]modified[/]", escape(detail))
    for item in result["added"]:
        table.add_row(str(item["position"]), "[green]added[/]", escape(item["entry"]))
    for item in result["removed"]:
        table.add_row(str(item["position"]), "[red]removed[/]", escape(item["entry"]))
    console.print(table)
    console.print(
        f"[dim]a: {result['a_turns']} turns · b: {result['b_turns']} turns · "
        f"{result['identical_turns']} identical[/]"
    )


def _describe_change(change: dict) -> str:
    field = change["field"]
    if field == "call_added":
        return f"+{change['count']} call {change['call']}"
    if field == "call_removed":
        return f"-{change['count']} call {change['call']}"
    return f"{field}: {change['a']!r} → {change['b']!r}"


async def _show_session(session_id: str, fmt: str) -> None:
    client = KAGWebApp()
    session = await client.get_session(session_id)
    if session is None:
        console.print(f"[red]Session not found:[/] {session_id}")
        raise typer.Exit(code=1)

    if fmt == "json":
        console.print(json.dumps(session, ensure_ascii=False, indent=2, default=str))
        return

    console.print(f"[bold]{session.get('title', '')}[/] ({session.get('id', '')})")
    console.print(
        f"[dim]capability={session.get('capability', '') or 'chat'} "
        f"status={session.get('status', '')} "
        f"messages={len(session.get('messages', []))}[/]",
        highlight=False,
    )
    for message in session.get("messages", []):
        role = str(message.get("role", "")).upper()
        content = str(message.get("content", "") or "").strip()
        console.print(f"\n[cyan]{role}[/]")
        if content:
            console.print(content)


async def _delete_session(session_id: str) -> None:
    client = KAGWebApp()
    success = await client.delete_session(session_id)
    if not success:
        console.print(f"[red]Session not found:[/] {session_id}")
        raise typer.Exit(code=1)
    console.print(f"Deleted session {session_id}")


async def _rename_session(session_id: str, title: str) -> None:
    client = KAGWebApp()
    success = await client.rename_session(session_id, title)
    if not success:
        console.print(f"[red]Session not found:[/] {session_id}")
        raise typer.Exit(code=1)
    console.print(f"Renamed {session_id} -> {title}")
