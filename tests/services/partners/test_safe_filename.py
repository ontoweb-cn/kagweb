"""Regression tests for partners.helpers.safe_filename."""

from __future__ import annotations

import sys

import pytest

from pathlib import Path

from kagweb.partners.helpers import safe_filename


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX /tmp path")
def test_safe_filename_strips_embedded_null_byte() -> None:
    cleaned = safe_filename("photo\x00.png")
    assert "\x00" not in cleaned
    assert cleaned.endswith(".png")
    path = Path("/tmp") / f"abc_{cleaned}"
    path.write_bytes(b"x")
    assert path.read_bytes() == b"x"
    path.unlink()


def test_safe_filename_strips_newlines_and_dot_names() -> None:
    assert "\n" not in safe_filename("a\nb.png")
    assert safe_filename(".") == ""
    assert safe_filename("..") == ""
