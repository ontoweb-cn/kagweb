"""Phase 3a: slim TurnRequest to the shell-chat surface."""
import io
import re

import py_compile

p = "kagweb/core/turn_request.py"
s = io.open(p, encoding="utf-8").read()


def cut(a, b):
    global s
    i = s.index(a)
    j = s.index(b, i)
    s = s[:i] + s[j:]


def rep(old, new):
    global s
    assert old in s, f"NF: {old[:80]!r}"
    s = s.replace(old, new, 1)


# legacy config translation map entries for removed fields
s = re.sub(r'    "_course_id": "course_id",\n', "", s)
s = re.sub(r'    "subagent_consult_budget": "subagent_consult_budget",\n', "", s)
s = re.sub(r'    "auto_route": "auto_route",\n', "", s)

# dead payload models
cut("class NotebookReference(BaseModel):", "MemoryReference = Literal")

# dead fields
rep("""    notebook_references: list[NotebookReference] = Field(default_factory=list)
""", "")
rep("""    question_notebook_references: list[int] = Field(default_factory=list)
""", "")
rep("""    book_references: list[BookReference] = Field(default_factory=list)
""", "")
rep("""    reading_references: list[ReadingReference] = Field(default_factory=list)
""", "")
rep("""    memory_references: list[MemoryReference] = Field(default_factory=list)
""", "")
rep("""    skills: list[str] = Field(default_factory=list)
""", "")
rep("""    mastery_path_id: str | None = None
    mastery_path_lease_managed: bool = False
""", "")
rep("""    reading_material_id: str | None = None
    reading_material_revision: int | None = Field(default=None, ge=1)
    reading_workspace_id: str | None = None
    reading_viewport: ReadingViewport | None = None
""", "")
rep("""    timed_media_id: str | None = None
    timed_media_viewport: TimedMediaViewport | None = None
""", "")
rep("""    # Runtime options are explicit and never passed to a capability schema.
    course_id: str | None = None
""", "")
rep("""    followup_question_context: dict[str, Any] | None = None
""", "")
rep("""    subagent_consult_budget: int | None = Field(default=None, ge=0)
    auto_route: bool | None = None
""", "")

# MemoryReference alias (now unused)
rep('MemoryReference = Literal["recent", "profile", "scope", "preferences", "summary"]\n\n\n', "")

s = re.sub(r"\n{4,}", "\n\n\n", s)
io.open(p, "w", encoding="utf-8", newline="").write(s)
py_compile.compile(p, doraise=True)
print("turn_request slimmed;", s.count(chr(10)), "lines")
import subprocess

print(subprocess.run(["grep", "-n", "reading\\|notebook\\|book\\|mastery\\|timed\\|memory_ref\\|course_id\\|subagent\\|auto_route", "kagweb/core/turn_request.py"], capture_output=True, text=True).stdout or "clean")
