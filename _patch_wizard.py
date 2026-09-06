"""Careful init_wizard embedding removal (fresh from HEAD state)."""
import io
import re

import py_compile

p = "deepmentor_cli/init_wizard.py"
src = io.open(p, encoding="utf-8").read()

if "def probe_embedding(" not in src:
    print("already clean")
    raise SystemExit


def cut(src, start_pat, end_pat):
    i = re.search(start_pat, src, re.M).start()
    j = re.search(end_pat, src[i:], re.M).start() + i
    return src[:i] + src[j:]


src = cut(src, r"^def probe_embedding\(", r"^# --- Review panel|^def render_review_panel\(")
src = cut(src, r"^def fetch_embedding_models\(", r"^def select_model\(")
src = cut(src, r"^def _looks_like_embedding_model\(", r"^def fetch_embedding_models\(")
src = cut(src, r"^def _derive_embedding_models_url\(", r"^def _looks_like_embedding_model\(")
src = cut(src, r"^def select_embedding_provider\(", r"^def select_search_provider\(")
src = cut(src, r"^class EmbeddingChoice\b", r"^class SearchChoice:")
src = cut(src, r"^EMBEDDING_FALLBACK_MODELS:", r"^class SearchProviderSpec")
src = cut(src, r"^# Featured embedding providers", r"^class SearchProviderSpec")

src = re.sub(r"from deepmentor\.services\.config\.embedding_endpoint import \([^)]*\)\n", "", src, count=1)
for e in [
    '    "EMBEDDING_FALLBACK_MODELS",\n',
    '    "EmbeddingChoice",\n',
    '    "FEATURED_EMBEDDING_PROVIDERS",\n',
    '    "fetch_embedding_models",\n',
    '    "probe_embedding",\n',
    '    "select_embedding_provider",\n',
]:
    src = src.replace(e, "", 1)
src = src.replace(
    "    llm: LLMChoice | None,\n    embedding: EmbeddingChoice | None,\n",
    "    llm: LLMChoice | None,\n",
    1,
)
src = re.sub(r"    if embedding:\n        _row\(\n(?:.*\n)*?        \)\n", "", src, count=1)
src = src.replace('    # (embedding / search). LLM is mandatory', '    # (search). LLM is mandatory', 1)
src = src.replace(
    "    list in ``LLM_FALLBACK_MODELS`` / ``EMBEDDING_FALLBACK_MODELS``.",
    "    list in ``LLM_FALLBACK_MODELS``.",
    1,
)
src = re.sub(r"\n{4,}", "\n\n\n", src)
io.open(p, "w", encoding="utf-8", newline="").write(src)
py_compile.compile(p, doraise=True)
print("init_wizard OK,", src.count(chr(10)), "lines")
leftover = subprocess.run(["grep", "-niE", "embedding", "deepmentor_cli/init_wizard.py"], capture_output=True, text=True).stdout
print(leftover or "clean")
