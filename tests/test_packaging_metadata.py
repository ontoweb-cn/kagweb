"""Tests for dependency metadata shared by the published packages."""

from __future__ import annotations

from pathlib import Path
import tomllib

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def _project(path: Path) -> dict:
    with path.open("rb") as file:
        return tomllib.load(file)["project"]


def _cli_requirement_lines() -> list[str]:
    return [
        line.split("#", 1)[0].strip()
        for line in (REPOSITORY_ROOT / "requirements" / "cli.txt")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.split("#", 1)[0].strip()
    ]


def test_parser_extras_track_current_upstream_floors() -> None:
    extras = _project(REPOSITORY_ROOT / "pyproject.toml")["optional-dependencies"]

    assert extras["parse-markitdown"] == ["markitdown[all]>=0.1.7"]
    assert extras["parse-pymupdf4llm"] == ["pymupdf4llm>=1.28.2"]
    assert extras["parse-liteparse"] == ["liteparse>=2.14.2"]
    assert extras["parse-docling"] == [
        "docling[xbrl]>=2.123.1",
        "docling-slim[format-iwork,format-opendocument,format-video]>=2.123.1",
    ]


def test_python_314_is_supported_by_both_distributions() -> None:
    expected = ">=3.11,<3.15"
    assert _project(REPOSITORY_ROOT / "pyproject.toml")["requires-python"] == expected
    assert (
        _project(REPOSITORY_ROOT / "packaging" / "kagweb-cli" / "pyproject.toml")["requires-python"]
        == expected
    )


@pytest.mark.parametrize(
    "metadata_path",
    [
        REPOSITORY_ROOT / "pyproject.toml",
        REPOSITORY_ROOT / "packaging" / "kagweb-cli" / "pyproject.toml",
    ],
)
def test_typer_dependency_does_not_request_removed_all_extra(metadata_path: Path) -> None:
    with metadata_path.open("rb") as file:
        dependencies = tomllib.load(file)["project"]["dependencies"]

    typer_requirements = [item for item in dependencies if item.startswith("typer")]
    assert typer_requirements == ["typer>=0.9.0"]


def test_full_app_cron_dependency_matches_every_server_install_surface() -> None:
    expected = "croniter>=6.0.0,<7.0.0"
    with (REPOSITORY_ROOT / "pyproject.toml").open("rb") as file:
        project = tomllib.load(file)["project"]

    assert project["dependencies"].count(expected) == 1
    assert project["optional-dependencies"]["server"].count(expected) == 1
    assert (REPOSITORY_ROOT / "requirements" / "server.txt").read_text(
        encoding="utf-8"
    ).splitlines().count(expected) == 1


@pytest.mark.parametrize(
    "expected",
    [
        "loguru>=0.7.3,<1.0.0",
        "json-repair>=0.57.0,<1.0.0",
        "pyte>=0.8.1",
        "pdfplumber>=0.11.0",
        "reportlab>=4.0.0",
    ],
)
def test_cli_runtime_dependencies_match_every_install_surface(expected: str) -> None:
    """CLI-only installs must include everything used by terminal workflows."""
    with (REPOSITORY_ROOT / "pyproject.toml").open("rb") as file:
        root = tomllib.load(file)["project"]
    with (REPOSITORY_ROOT / "packaging" / "kagweb-cli" / "pyproject.toml").open("rb") as file:
        cli_package = tomllib.load(file)["project"]

    assert root["dependencies"].count(expected) == 1
    assert cli_package["dependencies"].count(expected) == 1
    requirement_lines = [
        line.split("#", 1)[0].strip()
        for line in (REPOSITORY_ROOT / "requirements" / "cli.txt")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert requirement_lines.count(expected) == 1
