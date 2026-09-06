"""Phase 2a patch: remove knowledge_bases / RAG / embedding remnants from keepers."""
import io
import re

import py_compile


def load(p):
    return io.open(p, encoding="utf-8").read()


def save(p, s):
    io.open(p, "w", encoding="utf-8", newline="").write(s)
    py_compile.compile(p, doraise=True)
    print("OK", p)


def rep(src, old, new, path=""):
    assert old in src, f"NOT FOUND in {path}: {old[:100]!r}"
    return src.replace(old, new, 1)


# ── core/turn_request.py: drop knowledge_bases field ──
p = "kagweb/core/turn_request.py"
s = load(p)
s = rep(s, "    knowledge_bases: list[str] = Field(default_factory=list)\n", "", p)
save(p, s)

# ── multi_user/grants.py: drop knowledge_bases grant key ──
p = "kagweb/multi_user/grants.py"
s = load(p)
s = rep(s, '        "knowledge_bases": [],\n', "", p)
s = rep(s, '    for key in ("knowledge_bases", "skills", "partners"):',
        '    for key in ("partners",):', p)
save(p, s)

# ── multi_user/paths.py: stop creating knowledge_bases dir ──
p = "kagweb/multi_user/paths.py"
s = load(p)
s = rep(s, '    (root / "knowledge_bases").mkdir(parents=True, exist_ok=True)\n', "", p)
save(p, s)

# ── path_service.py: drop get_knowledge_bases_root ──
p = "kagweb/services/path_service.py"
s = load(p)
s = rep(s, '''    def get_knowledge_bases_root(self) -> Path:
        return self._workspace_root / "knowledge_bases"

''', "", p)
save(p, s)

# ── config/defaults.py + schema.py + loader.py: drop knowledge_bases_dir ──
p = "kagweb/config/defaults.py"
s = load(p)
s = rep(s, '        "knowledge_bases_dir": str(_project_root / "data" / "knowledge_bases"),\n', "", p)
save(p, s)
p = "kagweb/config/schema.py"
s = load(p)
s = rep(s, "    knowledge_bases_dir: str\n", "", p)
save(p, s)
p = "kagweb/services/config/loader.py"
s = load(p)
s = rep(s, '        "knowledge_bases_dir": str(path_service.get_knowledge_bases_root()),\n', "", p)
save(p, s)

# ── session/legacy_migration.py: tolerate old kb prefs by dropping the key ──
p = "kagweb/services/session/legacy_migration.py"
s = load(p)
i = s.index('"knowledge_bases": (')
j = s.index("),", i) + 2
# show context first
print("--- legacy_migration context:")
print(s[max(0, i - 200):j + 50])
