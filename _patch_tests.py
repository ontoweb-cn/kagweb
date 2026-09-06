"""Phase 1 patch: prune workspace-mode and notebook tests from session stores."""
import io
import re

import py_compile


def drop_functions(path, names):
    src = io.open(path, encoding="utf-8").read()
    for name in names:
        pattern = re.compile(
            r"\n(?:@pytest\.mark\.asyncio\n)?(?:@pytest\.fixture\n)?"
            r"(?:async )?def " + re.escape(name) + r"\(.*?(?=\n(?:@pytest|def |async def |\Z))",
            re.S,
        )
        new = pattern.sub("\n", src, count=1)
        assert new != src, f"function not dropped: {name} in {path}"
        src = new
    src = re.sub(r"\n{4,}", "\n\n\n", src)
    io.open(path, "w", encoding="utf-8", newline="").write(src)
    py_compile.compile(path, doraise=True)
    print("OK", path)


drop_functions(
    "tests/services/session/test_sqlite_store.py",
    [
        "test_store_migrates_legacy_workspace_ownership_without_reordering",
        "test_explicit_workspace_migration_is_idempotent",
        "test_store_migrates_legacy_notebook_review_columns",
        "test_generic_history_lists_immersive_reading_sessions_with_their_collection",
        "test_upsert_notebook_entries_persists_all",
        "test_list_notebook_entries_intersects_session_filters",
        "test_upsert_notebook_entries_updates_on_conflict",
        "test_upsert_skips_blank_questions",
        "test_upsert_unknown_session_raises",
        "test_list_entries_filters_bookmarked",
        "test_list_entries_filters_is_correct",
        "test_notebook_review_metadata_filters_and_transitions",
        "test_question_bank_materials_respect_session_scope",
    ],
)

drop_functions(
    "tests/services/session/test_pocketbase_isolation.py",
    [
        "test_legacy_workspace_preferences_are_normalized_at_repository_boundary",
        "test_workspace_migration_persists_metadata_without_reordering",
    ],
)
