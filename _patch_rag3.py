"""Phase 2a patch stage 4: settings/system routers embedding+videogen removal."""
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


def cut(src, start, end, path=""):
    i = src.index(start)
    j = src.index(end, i)
    return src[:i] + src[j:]


# ── settings.py ──
p = "deepmentor/api/routers/settings.py"
s = load(p)
s = rep(s, '''    logger.warning(
        "Admin applied catalog; resetting global LLM/embedding clients. "
        "In-flight user turns may flip backend client mid-call."
    )
    from deepmentor.services.embedding.client import reset_embedding_client
    from deepmentor.services.llm.client import reset_llm_client

    clear_llm_config_cache()
    reset_llm_client()
    reset_embedding_client()''',
'''    logger.warning(
        "Admin applied catalog; resetting the global LLM client. "
        "In-flight user turns may flip backend client mid-call."
    )
    from deepmentor.services.llm.client import reset_llm_client

    clear_llm_config_cache()
    reset_llm_client()''', p)

s = rep(s, '''    embedding = sorted(
        [
            {
                "value": name,
                "label": spec.label,
                "base_url": spec.default_api_base,
                "default_dim": str(spec.default_dim) if spec.default_dim else "",
            }
            for name, spec in EMBEDDING_PROVIDERS.items()
            if name != "custom_openai_sdk"
        ],
        key=lambda p: p["label"].lower(),
    )
''', "", p)
s = rep(s, '''        "llm": llm,
        # Same shape, same vendors: the task service stands in for the LLM.
        "task": llm,
        "embedding": embedding,
        "search": search,''',
'''        "llm": llm,
        # Same shape, same vendors: the task service stands in for the LLM.
        "task": llm,
        "search": search,''', p)
save(p, s)
print("settings stage A done; _connection_targets handled next")
