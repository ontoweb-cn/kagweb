"""Cross-language parity: the Python DSL export must match the TypeScript one.

`fixtures/session_dsl_expected.json` and `.mmd` are generated from the web
implementation:

    npx tsx tests/services/session/fixtures/generate_parity.mts

This test is the Python half of that contract — the two implementations are
hand-maintained mirrors (Python cannot import TypeScript), so the fixture is
the only thing that keeps them from drifting. `expected.raw` is deliberately
not compared: it carries `exported_at` and is never stable.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from kagweb.services.session.dsl_export import build_session_dsl, dsl_to_mermaid

FIXTURES = Path(__file__).parent / "fixtures"


def _fixture() -> dict[str, Any]:
    return json.loads(
        (FIXTURES / "session_dsl_expected.json").read_text(encoding="utf-8")
    )


def _stable_doc(fixture: dict[str, Any]) -> dict[str, Any]:
    return build_session_dsl(fixture["input_messages"], stable=True, normalize_ids=True)


def test_stable_normalized_dsl_matches_the_typescript_fixture() -> None:
    fixture = _fixture()

    assert _stable_doc(fixture) == fixture["expected"]["stable_normalized"]


def test_mermaid_matches_the_typescript_fixture() -> None:
    fixture = _fixture()
    expected = (FIXTURES / "session_dsl_expected.mmd").read_text(encoding="utf-8")

    assert dsl_to_mermaid(_stable_doc(fixture)) == expected
