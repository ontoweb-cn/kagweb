"""Phase 2a patch stage 3c: provider_runtime embedding removal (corrected)."""
import io
import re

import py_compile

p = "kagweb/services/config/provider_runtime.py"
s = io.open(p, encoding="utf-8").read()
if "resolve_embedding_runtime_config" not in s:
    print("already patched")
    raise SystemExit


def cut_span(src, start_marker, end_marker):
    i = src.index(start_marker)
    j = src.index(end_marker, i)
    return src[:i] + src[j:]


def rep(src, old, new):
    global s
    assert old in src, f"NOT FOUND: {old[:90]!r}"
    s = src.replace(old, new, 1)


# 1. import block
s = cut_span(s, "from .embedding_endpoint import (", ")\n")
s = re.sub(r"\n\n\n+", "\n\n\n", s)

# 2. ResolvedEmbeddingConfig dataclass (class ... next top-level)
i = s.index("class ResolvedEmbeddingConfig:")
nxt = re.search(r"\n(?:class |def |@)", s[i + 10:])
end = i + 10 + nxt.start() + 1
s = s[:i] + s[end:]

# 3. _resolve_embedding_dimension .. keep _coerce_optional_bool (used elsewhere? check below)
s = cut_span(s, "def _resolve_embedding_dimension(", "def _coerce_optional_bool(")

# 4. _resolve_embedding_provider .. resolve_embedding_runtime_config end (next def = _coerce_optional_float)
s = cut_span(s, "def _resolve_embedding_provider(", "def _coerce_optional_float(")

# 5. _clamp_batch_size/_clamp_batch_delay (only used by embedding resolver)
i = s.index("def _clamp_batch_size(")
nxt = re.search(r"\n(?:class |def |@)", s[i + 10:])
end = i + 10 + nxt.start() + 1
s = s[:i] + s[end:]

# 6. __all__ entry
rep(s, '    "resolve_embedding_runtime_config",\n', "")

s = re.sub(r"\n{4,}", "\n\n\n", s)
io.open(p, "w", encoding="utf-8", newline="").write(s)
py_compile.compile(p, doraise=True)
print("provider_runtime OK,", s.count(chr(10)), "lines")
