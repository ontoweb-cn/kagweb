"""Phase 1 patch: chat.py REPL prune (cron, kb, notebook)."""
import io
import re

import py_compile

path = "deepmentor_cli/chat.py"
src = io.open(path, encoding="utf-8").read()


def rep(old, new):
    global src
    assert old in src, f"NOT FOUND: {old[:90]!r}"
    src = src.replace(old, new, 1)


def cut(start, end):
    global src
    i = src.index(start)
    j = src.index(end, i)
    src = src[:i] + src[j:]


# state fields
rep('''class ChatState:
    session_id: str | None = None
    capability: str = "chat"
    tools: list[str] = field(default_factory=list)
    knowledge_bases: list[str] = field(default_factory=list)
    language: str = "en"
    notebook_references: list[dict[str, Any]] = field(default_factory=list)
    history_references: list[str] = field(default_factory=list)
    config: dict[str, Any] = field(default_factory=dict)''',
    '''class ChatState:
    session_id: str | None = None
    capability: str = "chat"
    tools: list[str] = field(default_factory=list)
    language: str = "en"
    history_references: list[str] = field(default_factory=list)
    config: dict[str, Any] = field(default_factory=dict)''')

# CLI options
rep('''        kb: list[str] = typer.Option([], "--kb", help="Pre-attach knowledge base(s)."),
        notebook_ref: list[str] = typer.Option([], "--notebook-ref", help="Notebook references."),
        history_ref''', '''        history_ref''')

# preferences restore
rep('''        state.tools = list(preferences.get("tools") or state.tools)
        state.knowledge_bases = list(preferences.get("knowledge_bases") or state.knowledge_bases)
        state.language = str(preferences.get("language") or state.language)
        state.notebook_references = list(
            preferences.get("notebook_references") or state.notebook_references
        )
        state.history_references = list(''',
    '''        state.tools = list(preferences.get("tools") or state.tools)
        state.language = str(preferences.get("language") or state.language)
        state.history_references = list(''')

# help panel
rep('''            "  /tool on|off <name>\\n"
            "  /cap <name>\\n"
            "  /kb <name>|none\\n"
            "  /history add <id> | /history clear\\n"
            "  /notebook add <ref> | /notebook clear\\n"
            "  /show last|<n> — expand a tool result or captured thinking\\n"''',
    '''            "  /tool on|off <name>\\n"
            "  /cap <name>\\n"
            "  /history add <id> | /history clear\\n"
            "  /show last|<n> — expand a tool result or captured thinking\\n"''')

# turn request
rep('''            request = build_turn_request(
                content=user_input,
                capability=state.capability,
                session_id=state.session_id,
                tools=list(state.tools),
                knowledge_bases=list(state.knowledge_bases),
                language=state.language,
                config=dict(state.config),
                notebook_references=list(state.notebook_references),
                history_references=list(state.history_references),
            )''',
    '''            request = build_turn_request(
                content=user_input,
                capability=state.capability,
                session_id=state.session_id,
                tools=list(state.tools),
                language=state.language,
                config=dict(state.config),
                history_references=list(state.history_references),
            )''')
# fallback in case of whitespace variance
if "knowledge_bases=list(state.knowledge_bases)" in src:
    src = re.sub(r"\n\s+knowledge_bases=list\(state\.knowledge_bases\),", "", src, count=1)
    src = re.sub(r"\n\s+notebook_references=list\(state\.notebook_references\),", "", src, count=1)

# finally cron stop
rep('''    finally:
        if cron_service is not None:
            with suppress(Exception):
                await cron_service.stop()''',
    '''    finally:
        pass''')

io.open(path, "w", encoding="utf-8", newline="").write(src)
py_compile.compile(path, doraise=True)
print("chat.py OK")
