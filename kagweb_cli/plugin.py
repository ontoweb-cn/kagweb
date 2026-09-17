"""
CLI Plugin Command
==================

List and inspect registered capabilities.

There is no tool registry: the tool layer was removed along with the MCP
client stack, and an agent-loop backend carries its own tooling. These
commands used to read one, which made both of them fail outright once it
was gone.
"""

from __future__ import annotations

from dataclasses import asdict

from rich.console import Console
from rich.table import Table
import typer

console = Console()


def register(app: typer.Typer) -> None:
    @app.command("list")
    def plugin_list() -> None:
        """List all registered capabilities."""
        from kagweb.runtime.registry.capability_registry import get_capability_registry

        cr = get_capability_registry()

        table = Table(title="Registered Plugins")
        table.add_column("Name", style="bold")
        table.add_column("Type")
        table.add_column("Description")

        for m in cr.get_manifests():
            table.add_row(m["name"], m.get("kind", "capability"), m["description"][:80])

        console.print(table)

    @app.command("info")
    def plugin_info(name: str = typer.Argument(..., help="Capability name.")) -> None:
        """Show details of a capability."""
        import json

        from kagweb.runtime.registry.capability_registry import get_capability_registry

        cr = get_capability_registry()

        cap = cr.get(name)
        if cap:
            from kagweb.app import KAGWebApp

            availability = KAGWebApp().get_capability_availability(name)
            console.print_json(
                json.dumps(
                    {
                        "name": cap.manifest.name,
                        "description": cap.manifest.description,
                        "cli_aliases": cap.manifest.cli_aliases,
                        "stages": cap.manifest.stages,
                        "tools_used": cap.manifest.tools_used,
                        "config_defaults": cap.manifest.config_defaults,
                        "availability": asdict(availability),
                    },
                    indent=2,
                )
            )
            return

        console.print(f"[red]'{name}' not found.[/]")
        raise typer.Exit(code=1)
