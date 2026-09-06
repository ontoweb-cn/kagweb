"""Phase 1 patch (cont.): view.py, provider_runtime, test_runner, partners, chat, tools."""
import io
import re
import subprocess

import py_compile


def load(p):
    return io.open(p, encoding="utf-8").read()


def save(p, s):
    io.open(p, "w", encoding="utf-8", newline="").write(s)
    py_compile.compile(p, doraise=True)
    print("OK", p, f"({s.count(chr(10))} lines)")


def cut(src, start, end):
    i = src.index(start)
    j = src.index(end, i)
    return src[:i] + src[j:]


def rep(src, old, new, path=""):
    assert old in src, f"NOT FOUND in {path}: {old[:90]!r}"
    return src.replace(old, new, 1)


# ── runtime/providers/view.py ──
path = "deepmentor/runtime/providers/view.py"
src = load(path)
src = rep(src, "    cli_pool = _cli_app_tools(scope)\n", "", path)
src = cut(src, "def _cli_app_tools(scope: ToolScope) -> list[BaseTool]:", "def _user_grant(scope: ToolScope) -> Allowlist:")
save(path, src)

# ── services/config/provider_runtime.py ──
path = "deepmentor/services/config/provider_runtime.py"
src = load(path)
src = rep(src, "from deepmentor.services.videogen.config import VideogenConfig\n", "", path)
i = src.index("def resolve_videogen_runtime_config(")
nxt = re.search(r"\n(?:async )?def ", src[i + 10:])
j = i + 10 + nxt.start() + 1
src = src[:i] + src[j:]
src = rep(src, '    "resolve_videogen_runtime_config",\n', "", path)
save(path, src)

# ── services/config/test_runner.py ──
path = "deepmentor/services/config/test_runner.py"
src = load(path)
src = rep(src, """            elif service == "videogen":
                asyncio.run(self._test_videogen(run, catalog))
""", "", path)
i = src.index("    async def _test_videogen(")
nxt = re.search(r"\n    (?:async )?def ", src[i + 10:])
j = i + 10 + nxt.start() + 1
src = src[:i] + src[j:]
save(path, src)

# ── services/partners/manager.py ──
path = "deepmentor/services/partners/manager.py"
src = load(path)
src = rep(src, """        try:
            from deepmentor.services.cron import get_cron_service

            get_cron_service().remove_owner_jobs(f"partner:{partner_id}")
        except Exception:
            logger.warning("Failed to clear cron jobs for '%s'", partner_id, exc_info=True)
""", "", path)
save(path, src)

# ── services/partners/runtime.py: skills manifest -> empty; kb names -> empty ──
path = "deepmentor/services/partners/runtime.py"
src = load(path)
src = rep(src, '''    def _build_skills_manifest(self) -> str:
        try:
            from deepmentor.services.skill.service import (
                get_skill_service,
                render_skills_manifest,
            )

            service = get_skill_service()
            entries = service.summary_entries()
            always_block = service.load_always_for_context()
            return "\\n\\n".join(
                part for part in (always_block, render_skills_manifest(entries)) if part
            )
        except Exception:
            logger.warning(
                "Failed to build skills manifest for partner %s", self.partner_id, exc_info=True
            )
            return ""''',
          '''    def _build_skills_manifest(self) -> str:
        # Skills were removed with the capability layer; partners ship no
        # skill manifest.
        return ""''')
save(path, src)

print(subprocess.run(["grep", "-n", "_list_kb_names\\|services.skill\\|services.cron\\|cli_apps\\|videogen", "deepmentor/services/partners/runtime.py", "deepmentor/services/partners/workspace.py", "deepmentor_cli/chat.py", "deepmentor/tools/__init__.py"], capture_output=True, text=True).stdout)
