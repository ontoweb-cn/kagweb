"""Phase 2a patch stage 6: partners assets, doctor rag check, models, pb_setup, conftest."""
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


# ── partners.py: drop kb/skill/notebook asset payloads ──
p = "kagweb/api/routers/partners.py"
s = load(p)
s = rep(s, '''class AssetSpec(BaseModel):
    knowledge_bases: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    notebooks: list[str] = Field(default_factory=list)
''', '''class AssetSpec(BaseModel):
    """Deprecated asset-selection payload (assets were removed with the
    capability layer); accepted empty for client compatibility."""
''')
s = rep(s, '''        provisioning = provision_assets(
            partner_id,
            knowledge_bases=payload.assets.knowledge_bases,
            skills=payload.assets.skills,
            notebooks=payload.assets.notebooks,
        )''', '''        provisioning = provision_assets(partner_id)''')
s = rep(s, '''    report = provision_assets(
        partner_id,
        knowledge_bases=payload.knowledge_bases,
        skills=payload.skills,
        notebooks=payload.notebooks,
    )''', '''    report = provision_assets(partner_id)''')
# AssetAddRequest fields (payload.knowledge_bases etc.)
s = re.sub(r"    knowledge_bases: list\[str\] = Field\(default_factory=list\)\n", "", s)
s = re.sub(r"    skills: list\[str\] = Field\(default_factory=list\)\n", "", s)
s = re.sub(r"    notebooks: list\[str\] = Field\(default_factory=list\)\n", "", s)
save(p, s)

# ── multi_user/models.py: drop KnowledgeResource ──
p = "kagweb/multi_user/models.py"
s = load(p)
s = rep(s, '''@dataclass(frozen=True, slots=True)
class KnowledgeResource:
    id: str
    name: str
    base_dir: Path
    source: Literal["admin", "user"]
    assigned: bool = False
    read_only: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)
''', "")
import subprocess
print(subprocess.run(["grep", "-n", "KnowledgeResource", "kagweb/multi_user/models.py", "kagweb/multi_user/__init__.py"], capture_output=True, text=True).stdout)
print(subprocess.run(["grep", "-rn", "KnowledgeResource", "kagweb", "--include=*.py"], capture_output=True, text=True).stdout)
