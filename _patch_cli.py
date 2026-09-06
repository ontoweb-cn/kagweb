"""Phase 1 patch: CLI run command + common.py prune."""
import io
import py_compile

path = "deepmentor_cli/main.py"
src = io.open(path, encoding="utf-8").read()


def rep(old, new):
    global src
    assert old in src, f"NOT FOUND: {old[:70]!r}"
    src = src.replace(old, new, 1)


rep(
    '''            "Capability name (e.g. chat, deep_solve, deep_question, "
            "deep_research, visualize, math_animator, mastery_path)."''',
    '''            "Capability name (currently: chat)."''',
)
rep('''    tool: list[str] = typer.Option([], "--tool", "-t", help="Enabled tool(s)."),
    kb: list[str] = typer.Option([], "--kb", help="Knowledge base name."),
    notebook_ref: list[str] = typer.Option([], "--notebook-ref", help="Notebook references."),
    history_ref: list[str] = typer.Option([], "--history-ref", help="Referenced session ids."),
    language: str = typer.Option("en", "--language", "-l", help="Response language."),''',
    '''    tool: list[str] = typer.Option([], "--tool", "-t", help="Enabled tool(s)."),
    history_ref: list[str] = typer.Option([], "--history-ref", help="Referenced session ids."),
    language: str = typer.Option("en", "--language", "-l", help="Response language."),''')
rep('''        tools=tool,
        knowledge_bases=kb,
        language=language,
        config_items=config,
        config_json=config_json,
        notebook_refs=notebook_ref,
        history_refs=history_ref,
    )''',
    '''        tools=tool,
        language=language,
        config_items=config,
        config_json=config_json,
        history_refs=history_ref,
    )''')

io.open(path, "w", encoding="utf-8", newline="").write(src)
py_compile.compile(path, doraise=True)
print("main.py OK")

# common.py: drop kb/notebook plumbing
path = "deepmentor_cli/common.py"
src = io.open(path, encoding="utf-8").read()
i = src.index("def parse_notebook_references")
j = src.index("\n\n\n", i)
src = src[:i] + src[j+3:]
rep2_old = '''        tools: list[str],
'''
assert rep2_old in src
src = src.replace('''        tools: list[str],
''', '''        tools: list[str],
''', 1)
io.open(path, "w", encoding="utf-8", newline="").write(src)
print("common.py stage 1 (parse_notebook_references removed)")
