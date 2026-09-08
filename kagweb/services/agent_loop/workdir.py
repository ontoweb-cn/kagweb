"""Resolve a profile's working directory against the operator's allowed roots.

CLI agent loops are children of the KAGWeb process and run with its
privileges (see :mod:`kagweb.services.agent_loop.cli_backend`), so a free-form
``workdir`` would hand the loop every path the server can reach. A profile may
point at a directory, but only inside a root the operator has allowed;
anything else is refused and the caller falls back to the per-session
workspace. This mirrors the ``allowed_roots`` allowlist the ``run_code`` tool
carried before the product layer was stripped.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def _resolve(candidate: str, base: Path) -> Path:
    path = Path(candidate).expanduser()
    if not path.is_absolute():
        path = base / path
    # resolve() follows symlinks, which is the point: a link pointing outside
    # an allowed root must not smuggle the real target past the check.
    return path.resolve()


def normalize_workdir_roots(value: Any, *, default: str) -> list[str]:
    """Configured roots as a stripped, non-empty list of strings.

    A missing key (``None``) or a non-list means "not configured" and takes
    ``default``; an explicitly empty list is returned as-is, because clearing
    the list is how an operator forbids every per-profile workdir.
    """
    if not isinstance(value, list):
        return [default]
    return [str(root).strip() for root in value if str(root or "").strip()]


def resolve_workdir_roots(roots: list[str], *, base: Path) -> list[Path]:
    """Resolve every configured root against ``base``, dropping blanks."""
    resolved: list[Path] = []
    for root in roots or []:
        text = str(root or "").strip()
        if text:
            resolved.append(_resolve(text, base))
    return resolved


def resolve_allowed_workdir(workdir: str, roots: list[str], *, base: Path) -> Path | None:
    """The resolved workdir when it sits inside an allowed root, else ``None``.

    An empty ``workdir`` is "not configured" and returns ``None`` as well; the
    caller distinguishes the two cases by checking the raw string.
    """
    candidate = str(workdir or "").strip()
    if not candidate:
        return None
    resolved = _resolve(candidate, base)
    for allowed in resolve_workdir_roots(roots, base=base):
        if resolved == allowed or resolved.is_relative_to(allowed):
            return resolved
    return None


__all__ = [
    "normalize_workdir_roots",
    "resolve_allowed_workdir",
    "resolve_workdir_roots",
]
