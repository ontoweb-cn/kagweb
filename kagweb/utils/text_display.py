"""Helpers for user-facing text that models sometimes double-encode."""

from __future__ import annotations

import codecs
import re

# Match dense JSON-style ``\\uXXXX`` runs (3+ escapes), mirroring the web
# markdown decoder so mastery / ask_user cards do not leak literal escapes (#973).
_ESCAPED_UNICODE_RUN = re.compile(r"(?:\\u[0-9a-fA-F]{4}){3,}")


def decode_escaped_unicode_for_display(text: str) -> str:
    """Decode dense ``\\uXXXX`` runs when they clearly represent non-ASCII text."""
    if not text or "\\u" not in text:
        return text

    def _replace(match: re.Match[str]) -> str:
        run = match.group(0)
        try:
            decoded = codecs.decode(run, "unicode_escape")
        except (UnicodeDecodeError, ValueError):
            return run
        if any(ord(ch) > 0x7F for ch in decoded):
            return decoded
        return run

    return _ESCAPED_UNICODE_RUN.sub(_replace, text)


#: Appended when a label is cut short, so a reader can tell it continues.
ELLIPSIS = "…"


def first_line_label(value: object, *, limit: int) -> str:
    """Collapse a backend-supplied name to one short, single-line label.

    Approvals name the action inside a sentence (``The agent wants to run
    "…"``), but agents routinely put a whole command transcript there —
    multi-line, thousands of characters. Only the first non-empty line is
    kept, and it is clamped to ``limit`` so the sentence cannot be blown up.

    The caller is responsible for showing the untruncated text somewhere
    (an approval must still reveal what it will run); this only makes the
    name readable.
    """
    text = str(value or "")
    if not text.strip():
        return ""
    line = next((part.strip() for part in text.splitlines() if part.strip()), "")
    if len(line) <= limit:
        return line
    return line[: limit - 1].rstrip() + ELLIPSIS


def untruncated_stem(label: str) -> str:
    """The pre-clamp form of a label, for comparing it against its source."""
    text = str(label or "")
    return text[: -len(ELLIPSIS)].rstrip() if text.endswith(ELLIPSIS) else text


__all__ = [
    "ELLIPSIS",
    "decode_escaped_unicode_for_display",
    "first_line_label",
    "untruncated_stem",
]
