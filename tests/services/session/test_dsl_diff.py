"""Session DSL structural diff (module 15, impl-review #67)."""

from __future__ import annotations

import json

from deepmentor.services.session.dsl_diff import diff_session_dsl, is_empty


def _doc(trace: list[dict]) -> dict:
    return {"version": 1, "generator": "deepmentor/session-dsl", "trace": trace}


def _user(node: str, preview: str = "q") -> dict:
    return {"node": node, "kind": "user", "text_preview": preview}


def _assistant(node: str, calls: list[dict] | None = None, capability: str = "chat") -> dict:
    entry: dict = {"node": node, "kind": "assistant", "capability": capability}
    if calls:
        entry["calls"] = calls
    return entry


def _round(round_index: int, calls: list[dict] | None = None) -> dict:
    entry: dict = {"node": f"r{round_index}", "kind": "round", "round_index": round_index}
    if calls:
        entry["calls"] = calls
    return entry


def _tool(name: str) -> dict:
    return {"node": f"t:{name}", "kind": "tool_call", "tool": name}


def test_identical_documents_produce_empty_diff() -> None:
    doc = _doc([_user("turn:1"), _assistant("turn:2", [_round(0, [_tool("rag")])])])
    result = diff_session_dsl(doc, json.loads(json.dumps(doc)))
    assert is_empty(result)
    assert result["identical_turns"] == 2


def test_text_preview_and_capability_changes_are_reported() -> None:
    a = _doc([_user("turn:1", "old question"), _assistant("turn:2")])
    b = _doc([_user("turn:1", "new question"), _assistant("turn:2", capability="deep_solve")])
    result = diff_session_dsl(a, b)
    assert result["modified"] == [
        {
            "position": 1,
            "node": "turn:1",
            "changes": [{"field": "text_preview", "a": "old question", "b": "new question"}],
        },
        {
            "position": 2,
            "node": "turn:2",
            "changes": [{"field": "capability", "a": "chat", "b": "deep_solve"}],
        },
    ]


def test_call_multiset_ignores_reorder_but_reports_add_remove() -> None:
    a = _doc([_assistant("turn:1", [_round(0, [_tool("rag"), _tool("web_search")])])])
    # Same two tools, reversed order → no diff.
    reordered = _doc(
        [_assistant("turn:1", [_round(0, [_tool("web_search"), _tool("rag")])])]
    )
    assert is_empty(diff_session_dsl(a, reordered))

    # One tool swapped for another → both removal and addition.
    b = _doc([_assistant("turn:1", [_round(0, [_tool("rag"), _tool("reason")])])])
    result = diff_session_dsl(a, b)
    changes = result["modified"][0]["changes"]
    assert {"field": "call_removed", "call": "web_search", "count": 1} in changes
    assert {"field": "call_added", "call": "reason", "count": 1} in changes


def test_nested_calls_flatten_into_fingerprints() -> None:
    a = _doc(
        [
            _assistant(
                "turn:1",
                [_round(0, [_tool("rag"), {"node": "s1", "kind": "subagent"}])],
            )
        ]
    )
    b = _doc([_assistant("turn:1", [_round(0, [_tool("rag")])])])
    changes = diff_session_dsl(a, b)["modified"][0]["changes"]
    assert changes == [{"field": "call_removed", "call": "subagent", "count": 1}]


def test_length_mismatch_reports_added_and_removed_turns() -> None:
    a = _doc([_user("turn:1"), _assistant("turn:2"), _user("turn:3")])
    b = _doc([_user("turn:1")])
    result = diff_session_dsl(a, b)
    assert result["removed"] == [
        {"position": 2, "entry": "assistant turn:2 (chat)"},
        {"position": 3, "entry": "user turn:3: q"},
    ]
    assert result["added"] == []
    assert result["identical_turns"] == 1

    result = diff_session_dsl(b, a)
    assert result["added"] == [
        {"position": 2, "entry": "assistant turn:2 (chat)"},
        {"position": 3, "entry": "user turn:3: q"},
    ]
    assert result["removed"] == []


def test_empty_traces_pair_cleanly() -> None:
    result = diff_session_dsl(_doc([]), _doc([]))
    assert is_empty(result)
    assert result == {
        "a_turns": 0,
        "b_turns": 0,
        "identical_turns": 0,
        "modified": [],
        "added": [],
        "removed": [],
    }


# ---------------------------------------------------------------------------
# CLI surface
# ---------------------------------------------------------------------------


def _invoke_diff(tmp_path, doc_a: dict, doc_b: dict, *args: str):
    from typer.testing import CliRunner

    from deepmentor_cli.main import app

    path_a = tmp_path / "a.json"
    path_b = tmp_path / "b.json"
    path_a.write_text(json.dumps(doc_a), encoding="utf-8")
    path_b.write_text(json.dumps(doc_b), encoding="utf-8")
    return CliRunner().invoke(app, ["session", "diff", str(path_a), str(path_b), *args])


def test_cli_diff_json_output(tmp_path) -> None:
    a = _doc([_user("turn:1", "old"), _assistant("turn:2")])
    b = _doc([_user("turn:1", "new")])
    result = _invoke_diff(tmp_path, a, b, "--json")
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["modified"][0]["changes"][0]["field"] == "text_preview"
    assert payload["removed"] == [{"position": 2, "entry": "assistant turn:2 (chat)"}]


def test_cli_diff_rich_table_renders_changes(tmp_path) -> None:
    a = _doc([_user("turn:1", "old")])
    b = _doc([_user("turn:1", "new")])
    result = _invoke_diff(tmp_path, a, b)
    assert result.exit_code == 0
    assert "modified" in result.output
    assert "text_preview" in result.output


def test_cli_diff_identical_documents(tmp_path) -> None:
    doc = _doc([_user("turn:1"), _assistant("turn:2")])
    result = _invoke_diff(tmp_path, doc, doc)
    assert result.exit_code == 0
    assert "No differences" in result.output


def test_cli_diff_rejects_missing_file(tmp_path) -> None:
    from typer.testing import CliRunner

    from deepmentor_cli.main import app

    missing = tmp_path / "missing.json"
    ok = tmp_path / "ok.json"
    ok.write_text(json.dumps(_doc([])), encoding="utf-8")
    result = CliRunner().invoke(app, ["session", "diff", str(missing), str(ok)])
    assert result.exit_code == 1
    assert "File not found" in result.output


def test_cli_diff_rejects_non_dsl_json(tmp_path) -> None:
    from typer.testing import CliRunner

    from deepmentor_cli.main import app

    not_dsl = tmp_path / "not_dsl.json"
    not_dsl.write_text('{"hello": "world"}', encoding="utf-8")
    ok = tmp_path / "ok.json"
    ok.write_text(json.dumps(_doc([])), encoding="utf-8")
    result = CliRunner().invoke(app, ["session", "diff", str(not_dsl), str(ok)])
    assert result.exit_code == 1
    assert "Not a Session DSL export" in result.output
