"""Filename sanitization for stored attachments.

Lives outside the partner channel layer because ordinary chat attachments
(``services/storage/attachment_store.py``) sanitize the same way — and the
workspace materializer (``services/session/attachment_workspace.py``) reuses
``coerce_filename`` so a copy's name is always derivable from the store's.
"""

import os
import re

_UNSAFE_CHARS = re.compile(r'[<>:"/\\|?*]')
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")


def safe_filename(name: str) -> str:
    """Replace unsafe path / control characters; ``.`` / ``..`` become empty."""
    cleaned = _CONTROL_CHARS.sub("", _UNSAFE_CHARS.sub("_", name or "")).strip().strip(".")
    return cleaned


def coerce_filename(name: str) -> str:
    """Reduce *name* to a safe basename.

    * Strips any directory components (defends against ``../`` traversal).
    * Replaces filesystem-unsafe characters via :func:`safe_filename`.
    * Falls back to ``"file"`` if the result is empty.
    """
    base = os.path.basename(name or "")
    cleaned = safe_filename(base)
    return cleaned or "file"
