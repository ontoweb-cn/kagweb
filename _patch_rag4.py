"""Phase 2a patch stage 5: CLI init wizard embedding step removal."""
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


# ── init_cmd.py ──
p = "deepmentor_cli/init_cmd.py"
s = load(p)
# remove _embedding_default_endpoint + _embedding_step (up to the next top-level def after _embedding_step)
i = s.index("def _embedding_default_endpoint(")
nxt = re.search(r"\n(?:async )?def ", s[i + 10:])
j = i + 10 + nxt.start() + 1
s = s[:i] + s[j:]
s = rep(s, '''        # --- Step 3: Embedding (skip via [s] inside the picker) ---
        embedding_choice: wiz.EmbeddingChoice | None = None
        step_num += 1
        wiz.step_header(
            console, strings["init.step_embedding"].format(n=step_num, total=total_steps)
        )
        embedding_choice = _embedding_step(console, strings, catalog, llm_choice.api_key)
        if embedding_choice is not None:
            emb_profile, emb_model = _ensure_model_service(
                catalog,
                "embedding",
                "embedding-profile-default",
                "embedding-model-default",
            )
            emb_profile["binding"] = embedding_choice.binding
            emb_profile["base_url"] = embedding_choice.base_url
            emb_profile["api_key"] = embedding_choice.api_key
            emb_model["model"] = embedding_choice.model
            emb_model["name"] = embedding_choice.model or "Default Embedding Model"
            if embedding_choice.dimension:
                emb_model["dimension"] = embedding_choice.dimension''',
'''        # --- Step 3: Search (skip via [s] inside the picker) ---''')
s = rep(s, '''        # --- Step 4: Search (skip via [s] inside the picker) ---
        search_choice''', '''        search_choice''')
s = rep(s, '''            llm=llm_choice,
            embedding=embedding_choice,
            search=search_choice,''', '''            llm=llm_choice,
            search=search_choice,''')
s = s.replace("embedding → review) that writes the same files as the Web Settings page.",
              "search → review) that writes the same files as the Web Settings page.")
save(p, s)

# ── init_wizard.py: EmbeddingChoice + featured providers + step renderer ──
p = "deepmentor_cli/init_wizard.py"
s = load(p)
print("--- wizard embedding surface:")
import subprocess
print(subprocess.run(["grep", "-n", "EmbeddingChoice\\|embedding\\|FEATURED_EMBEDDING\\|EMBEDDING_FALLBACK", "deepmentor_cli/init_wizard.py"], capture_output=True, text=True).stdout)
