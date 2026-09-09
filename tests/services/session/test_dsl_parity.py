"""Cross-language parity: the Python DSL export must match the TypeScript one.

`fixtures/session_dsl_expected.json` and `.mmd` are generated from the web
implementation:

    npx tsx --tsconfig web/tsconfig.json tests/services/session/fixtures/generate_parity.mts

This test is the Python half of that contract — the two implementations are
hand-maintained mirrors (Python cannot import TypeScript), so the fixture is
the only thing that keeps them from drifting. Branch-level behaviour is pinned
by `test_dsl_export_call_groups.py` (Python) and
`web/tests/trace-selectors.test.ts` (TypeScript).
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from kagweb.services.session.dsl_export import build_session_dsl, dsl_to_mermaid

FIXTURES = Path(__file__).parent / "fixtures"


def _fixture() -> dict[str, Any]:
    return json.loads((FIXTURES / "session_dsl_expected.json").read_text(encoding="utf-8"))


def _stable_doc(fixture: dict[str, Any]) -> dict[str, Any]:
    return build_session_dsl(fixture["input_messages"], stable=True, normalize_ids=True)


def test_stable_normalized_dsl_matches_the_typescript_fixture() -> None:
    fixture = _fixture()

    assert _stable_doc(fixture) == fixture["expected"]["stable_normalized"]


def test_mermaid_matches_the_typescript_fixture() -> None:
    fixture = _fixture()
    expected = (FIXTURES / "session_dsl_expected.mmd").read_text(encoding="utf-8")

    assert dsl_to_mermaid(_stable_doc(fixture)) == expected


def test_raw_dsl_matches_the_typescript_fixture() -> None:
    """Raw is the mode both real consumers default to — the REST endpoint
    (`sessions.py`: ``stable=False, normalize_ids=False``) and the CLI facade
    (`facade.py`). It is also where the kind-encoding node ids, ``duration_ms``,
    ``error`` and ``turn_count`` are emitted, none of which appear in the
    stable document. ``exported_at`` is wall-clock on both sides, so it is the
    one field dropped."""
    fixture = _fixture()
    doc = build_session_dsl(fixture["input_messages"], session_id="s1")
    expected = copy.deepcopy(fixture["expected"]["raw"])

    assert doc["session"]["id"] == expected["session"]["id"]
    assert doc["session"]["turn_count"] == expected["session"]["turn_count"]
    doc["session"].pop("exported_at", None)
    expected["session"].pop("exported_at", None)

    assert doc == expected
