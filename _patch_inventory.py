"""One-shot Phase 1 patch: source_inventory _collect_from_user_message prune."""
import io
import py_compile

path = "deepmentor/services/session/source_inventory.py"
with io.open(path, "r", encoding="utf-8") as f:
    src = f.read()


def cut(src, start, end):
    i = src.index(start)
    j = src.index(end, i)
    return src[:i] + src[j:]


# notebook / book / reading blocks in the historical collector
src = cut(
    src,
    """    # Notebook records — re-resolve through the notebook service.""",
    """    # History sessions — async, one store fetch per id.""",
)

# question-bank block at the end of the collector
src = cut(
    src,
    """    # Question-bank entries.
    for raw in snap.get("questionNotebookReferences") or []:
        try:
            eid = int(raw)
        except (TypeError, ValueError):
            continue
        sid = f"qb-{eid}"
        if sid in inv:
            continue
        block, stem = await _load_question_entry(store, eid)
        if not block:
            continue
        inv.add(
            SourceEntry(
                sid=sid,
                kind="question",
                name=stem,
                full_text=block,
                fresh=False,
                first_seen_turn=turn_ordinal,
            )
        )
""",
    "",
)

with io.open(path, "w", encoding="utf-8", newline="") as f:
    f.write(src)
py_compile.compile(path, doraise=True)
print("collector OK, lines:", src.count(chr(10)))
