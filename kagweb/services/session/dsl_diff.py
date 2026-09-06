"""Structural diff between two exported Session DSL documents.

``kagweb session diff <a.json> <b.json>`` compares reasoning-chain
exports to answer "what changed between these two runs?" — e.g. a retry
with different tools, or the same session exported before/after a turn.

Pairing strategy (impl-review #67): trace entries align **by position**.
Same-length traces pair exactly; when lengths differ the first ``min(n)``
entries pair up and the remainder is reported as whole added/removed
turns. This deliberately avoids LCS alignment: it is simple, predictable,
and sufficient for the intended workflow (export both sides with
``--stable --normalize-ids`` and compare). The known limitation — an
insert/delete in the middle shifts every later pair — is documented in
the CLI ``--help``.

Call trees are compared as multisets of ``(kind, tool, round_index)``
fingerprints (flattened recursively), so reordered rounds/tools don't
flag as changes while genuinely added/removed calls do.

Pure analysis: no store access, no LLM, no filesystem — the CLI layer
reads the files.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

_FINGERPRINT_FIELDS = ("kind", "tool", "round_index")
_ENTRY_FIELDS = ("kind", "capability", "text_preview")

CallFingerprint = tuple[str, str, int | None]


def _call_fingerprints(calls: list[dict[str, Any]] | None) -> list[CallFingerprint]:
    """Flatten a (nested) call tree into fingerprint list — order kept for
    Counter semantics but not for comparison, i.e. reordering is not a diff."""
    fingerprints: list[CallFingerprint] = []

    def walk(nodes: list[dict[str, Any]] | None) -> None:
        for node in nodes or []:
            fingerprint = (
                str(node.get("kind") or ""),
                str(node.get("tool") or ""),
                node.get("round_index") if isinstance(node.get("round_index"), int) else None,
            )
            fingerprints.append(fingerprint)
            walk(node.get("calls"))

    walk(calls)
    return fingerprints


def _describe_fingerprint(fingerprint: CallFingerprint) -> str:
    kind, tool, round_index = fingerprint
    if kind == "round":
        return f"round {round_index + 1}" if round_index is not None else "round"
    if kind == "retrieve":
        return "retrieve"
    if kind == "subagent":
        return "subagent"
    return tool or kind or "call"


def _diff_entry(a: dict[str, Any], b: dict[str, Any]) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    for field in _ENTRY_FIELDS:
        if a.get(field) != b.get(field):
            changes.append({"field": field, "a": a.get(field), "b": b.get(field)})

    a_counts = Counter(_call_fingerprints(a.get("calls")))
    b_counts = Counter(_call_fingerprints(b.get("calls")))
    for fingerprint, count in (a_counts - b_counts).items():
        changes.append(
            {
                "field": "call_removed",
                "call": _describe_fingerprint(fingerprint),
                "count": count,
            }
        )
    for fingerprint, count in (b_counts - a_counts).items():
        changes.append(
            {
                "field": "call_added",
                "call": _describe_fingerprint(fingerprint),
                "count": count,
            }
        )
    return changes


def _entry_summary(entry: dict[str, Any]) -> str:
    kind = str(entry.get("kind") or "")
    node = str(entry.get("node") or "")
    if kind == "user":
        preview = str(entry.get("text_preview") or "")
        return f"user {node}: {preview}" if preview else f"user {node}"
    capability = str(entry.get("capability") or "")
    return f"assistant {node} ({capability})" if capability else f"assistant {node}"


def diff_session_dsl(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    """Diff two parsed DSL documents. Returns a JSON-ready summary dict."""
    trace_a = a.get("trace") if isinstance(a.get("trace"), list) else []
    trace_b = b.get("trace") if isinstance(b.get("trace"), list) else []

    modified: list[dict[str, Any]] = []
    identical = 0
    for position, (entry_a, entry_b) in enumerate(zip(trace_a, trace_b), start=1):
        changes = _diff_entry(entry_a, entry_b)
        if not changes:
            identical += 1
            continue
        modified.append(
            {
                "position": position,
                "node": str(entry_a.get("node") or ""),
                "changes": changes,
            }
        )

    added = [
        {"position": position, "entry": _entry_summary(entry)}
        for position, entry in enumerate(trace_b[len(trace_a) :], start=len(trace_a) + 1)
    ]
    removed = [
        {"position": position, "entry": _entry_summary(entry)}
        for position, entry in enumerate(trace_a[len(trace_b) :], start=len(trace_b) + 1)
    ]

    return {
        "a_turns": len(trace_a),
        "b_turns": len(trace_b),
        "identical_turns": identical,
        "modified": modified,
        "added": added,
        "removed": removed,
    }


def is_empty(result: dict[str, Any]) -> bool:
    return not (result["modified"] or result["added"] or result["removed"])
