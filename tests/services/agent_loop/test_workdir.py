"""Workdir allowlist resolution."""

from __future__ import annotations

from pathlib import Path

from kagweb.services.agent_loop.workdir import resolve_allowed_workdir


def test_empty_workdir_is_unconfigured(tmp_path: Path) -> None:
    assert resolve_allowed_workdir("", ["data/user"], base=tmp_path) is None
    assert resolve_allowed_workdir("   ", ["data/user"], base=tmp_path) is None


def test_relative_workdir_resolves_against_the_base(tmp_path: Path) -> None:
    resolved = resolve_allowed_workdir("data/user/projects/app", ["data/user"], base=tmp_path)
    assert resolved == (tmp_path / "data/user/projects/app").resolve()


def test_absolute_workdir_inside_an_absolute_root_is_allowed(tmp_path: Path) -> None:
    project = tmp_path / "projects"
    resolved = resolve_allowed_workdir(str(project / "app"), [str(project)], base=tmp_path)
    assert resolved == (project / "app").resolve()


def test_workdir_outside_every_root_is_refused(tmp_path: Path) -> None:
    assert resolve_allowed_workdir("elsewhere/app", ["data/user"], base=tmp_path) is None


def test_sibling_prefix_is_not_inside_the_root(tmp_path: Path) -> None:
    """ "data/user2" must not pass for the root "data/user"."""
    assert resolve_allowed_workdir("data/user2", ["data/user"], base=tmp_path) is None


def test_blank_roots_are_ignored(tmp_path: Path) -> None:
    assert resolve_allowed_workdir("data/user", ["", "  "], base=tmp_path) is None
