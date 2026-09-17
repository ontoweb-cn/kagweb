"""Contracts that keep the public docs aligned with the CLI surface."""

from __future__ import annotations

from pathlib import Path
import re
import shlex

ROOT = Path(__file__).resolve().parents[2]
DOCS_ROOT = ROOT / "site" / "src" / "content" / "docs"
PUBLIC_DOCS = (
    ROOT / "README.md",
    ROOT / "kagweb_cli" / "README.md",
    ROOT / "SKILL.md",
)


def _command_doc_paths() -> list[Path]:
    paths: list[Path] = []
    if DOCS_ROOT.exists():
        paths.extend(DOCS_ROOT.rglob("*.md"))
    paths.extend(path for path in PUBLIC_DOCS if path.exists())
    return paths


def _docs_text() -> str:
    return "\n".join(path.read_text(encoding="utf-8") for path in _command_doc_paths())


def _doc_ids() -> set[str]:
    ids: set[str] = set()
    for path in DOCS_ROOT.rglob("*.md"):
        slug = path.relative_to(DOCS_ROOT).with_suffix("").as_posix()
        ids.add(f"/{slug}")
        ids.add(f"/{slug}/")
        if slug.endswith("/index"):
            base = slug[: -len("/index")]
            ids.add(f"/{base}")
            ids.add(f"/{base}/")
    return ids


def _kagweb_commands() -> list[str]:
    commands: list[str] = []
    pending = ""
    for path in _command_doc_paths():
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not pending and not stripped.startswith("kagweb "):
                continue
            continued = stripped.endswith("\\")
            line_part = stripped[:-1].strip() if continued else stripped
            pending = f"{pending} {line_part}".strip()
            if continued:
                continue
            commands.append(pending)
            pending = ""
    return commands


def test_internal_docs_links_point_to_existing_pages() -> None:
    ids = _doc_ids()
    missing: list[tuple[str, str]] = []

    for path in DOCS_ROOT.rglob("*.md"):
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(r"\[[^\]]+\]\((/docs/[^)\s#]+)(?:#[^)]+)?\)", text):
            href = match.group(1)
            if href not in ids:
                missing.append((str(path.relative_to(ROOT)), href))

    assert missing == []


def _registered_commands() -> dict[str, list[str]]:
    """The CLI's real surface, read from the app rather than a copied list.

    A hand-maintained allowlist is what let this file keep blessing commands
    that no longer existed (`kb`, `memory`, `notebook`, `partner`, `skill`,
    `book`): the docs and the list drifted together while the app moved on, so
    the contract passed for a CLI that had been deleted. Introspection makes
    that class of drift impossible.
    """
    import typer

    from kagweb_cli.main import app

    group = typer.main.get_command(app)
    registered: dict[str, list[str]] = {}
    for name, command in group.commands.items():
        sub = getattr(command, "commands", None)
        registered[name] = sorted(sub.keys()) if sub else []
    return registered


def test_documented_kagweb_subcommands_exist() -> None:
    registered = _registered_commands()
    top_level = set(registered)
    provider_subcommands = set(registered.get("provider", []))

    for command in _kagweb_commands():
        first_segment = command.split("|", 1)[0].split("#", 1)[0].strip()
        if "<" in first_segment or "[" in first_segment:
            continue
        tokens = shlex.split(first_segment)
        if len(tokens) < 2:
            continue
        assert tokens[1] in top_level, command
        if tokens[1] == "provider" and len(tokens) >= 3:
            assert tokens[2] in provider_subcommands, command


def test_single_capability_cli_keeps_its_run_examples_actionable() -> None:
    """`run` examples must name a capability that exists.

    `chat` is the only built-in capability, so the docs previously advertised
    six that had been removed along with their subsystems. Any `run` example is
    checked against the registry instead of against a remembered name.
    """
    from kagweb.runtime.registry.capability_registry import get_capability_registry

    available = set(get_capability_registry().list_capabilities())
    assert available, "the registry should expose at least one capability"

    examples = [command for command in _kagweb_commands() if "kagweb run " in command]
    assert examples, "docs should include at least one `run` example"

    for command in examples:
        tokens = shlex.split(command.split("|", 1)[0].split("#", 1)[0].strip())
        # kagweb run <capability> "<message>"
        if len(tokens) < 3 or tokens[1] != "run" or tokens[2].startswith(("<", "[")):
            continue
        assert tokens[2] in available, command


def test_docs_do_not_advertise_removed_cli_forms() -> None:
    text = _docs_text()

    assert "kagweb provider logout" not in text
    assert "kagweb memory show summary" not in text
    assert "WS /api/turns" not in text
    # Subsystems removed whole — the docs must not still promise them.
    for removed in ("kagweb kb ", "kagweb notebook ", "kagweb memory ", "kagweb partner "):
        assert removed not in text, removed
