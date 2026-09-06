"""Comprehensive Phase 1 patch: multi_user router prune (all stages)."""
import io
import py_compile

path = "kagweb/api/routers/multi_user.py"
with io.open(path, "r", encoding="utf-8") as f:
    src = f.read()


def cut(src, start, end):
    i = src.index(start)
    j = src.index(end, i)
    return src[:i] + src[j:]


def rep(src, old, new, count=1):
    assert old in src, f"NOT FOUND: {old[:90]!r}"
    return src.replace(old, new, count)


# ── imports ──
src = rep(
    src,
    """from kagweb.api.routers.auth import require_admin, require_auth
from kagweb.knowledge.manager import KnowledgeBaseManager
from kagweb.multi_user.audit import log_admin_action, log_guardian_action
from kagweb.multi_user.book_permission import (
    BookDefaultLevel,
    BookPermission,
    BookPermissionLevel,
)
from kagweb.multi_user.context import get_current_user""",
    """from kagweb.api.routers.auth import require_admin, require_auth
from kagweb.multi_user.audit import log_admin_action, log_guardian_action
from kagweb.multi_user.context import get_current_user""",
)
src = rep(
    src,
    """from kagweb.multi_user.identity import (
    get_user_by_id,
    list_user_info,
    set_book_permission,
    set_password,
)
from kagweb.multi_user.knowledge_access import admin_kb_base_dir
from kagweb.multi_user.model_access import is_owner_bound""",
    """from kagweb.multi_user.identity import (
    get_user_by_id,
    list_user_info,
    set_password,
)
from kagweb.multi_user.model_access import is_owner_bound""",
)
src = rep(
    src,
    """from kagweb.reading import ReadingStore
from kagweb.reading.extensions import get_reading_extension_registry
from kagweb.services.auth import POCKETBASE_ENABLED, hash_password
from kagweb.services.config.model_catalog import ModelCatalogService
from kagweb.services.skill.service import SkillService
""",
    """from kagweb.services.auth import POCKETBASE_ENABLED, hash_password
from kagweb.services.config.model_catalog import ModelCatalogService
""",
)

# ── payload models ──
src = cut(src, "class BookPermissionPayload(BaseModel):", "class GuardianAuthorizationPayload(BaseModel):")
src = cut(src, "class GuardianMaterialsPayload(BaseModel):", "class GuardianRestrictionsPayload(BaseModel):")

# ── helpers: kb/skill summaries ──
src = cut(src, "def _admin_kb_summary() -> list[dict[str, Any]]:", "def _admin_partner_summary() -> list[dict[str, Any]]:")

# ── helpers: reading/staging/validation ──
src = cut(src, "def _reading_root(service: Any) -> Path:", "def _require_assignable_user(user_id: str) -> tuple[str, dict[str, Any]]:")

# ── /admin/resources trim ──
src = rep(
    src,
    '''    """Everything an admin can assign to a user: models, KBs, skills, and''',
    '''    """Everything an admin can assign to a user: models and''',
)
src = rep(
    src,
    """        "knowledge_bases": _admin_kb_summary(),
        "skills": _admin_skill_summary(),""",
    "",
)
src = cut(
    src,
    """        "reading_materials": _admin_reading_summary(),
        "reading_extensions": [
            extension.manifest.model_dump() for extension in get_reading_extension_registry().all()
        ],
""",
    "",
)

# ── /admin/books endpoint ──
src = cut(
    src,
    '''@router.get("/admin/books")''',
    '''@router.get("/guardians")''',
)

# ── guardian-report endpoint (books-centric) ──
src = cut(
    src,
    '''@router.get("/learners/{learner_user_id}/guardian-report")''',
    '''def _guardian_restrictions(grant: dict[str, Any]) -> dict[str, Any]:''',
)

# ── _guardian_restrictions strip reading ──
src = rep(
    src,
    '''    reading = policy.get("reading") if isinstance(policy.get("reading"), dict) else {}
    return {
        "age_band": policy.get("age_band"),
        "allow_upload": bool(reading.get("allow_upload", False)),
        "allowed_surfaces": list(policy.get("allowed_surfaces") or ["chat", "reading"]),
        "extensions": list(reading.get("extensions") or []),
    }''',
    '''    return {
        "age_band": policy.get("age_band"),
        "allowed_surfaces": [
            s for s in (policy.get("allowed_surfaces") or ["chat"]) if s != "reading"
        ],
    }''',
)

# ── restrictions GET: drop available_extensions ──
src = rep(
    src,
    '''    return {
        "restrictions": restrictions,
        "available_extensions": [
            extension.manifest.model_dump() for extension in get_reading_extension_registry().all()
        ],
    }''',
    '''    return {"restrictions": restrictions}''',
)

# ── restrictions PUT: drop extension validation + reading writes ──
src = rep(
    src,
    '''    available_extensions = {
        extension.manifest.id for extension in get_reading_extension_registry().all()
    }
    unknown_extensions = sorted(set(payload.extensions) - available_extensions)
    if unknown_extensions:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown reading extensions: {', '.join(unknown_extensions)}",
        )
    grant = deepcopy(_restriction_grant(learner_user_id, learner_record))''',
    '''    grant = deepcopy(_restriction_grant(learner_user_id, learner_record))''',
)
src = rep(
    src,
    '''    policy = grant.get("learning_policy")
    if not isinstance(policy, dict):
        raise HTTPException(status_code=409, detail="Learner account has no learning policy")
    reading = policy.get("reading")
    if not isinstance(reading, dict):
        reading = {}
        policy["reading"] = reading
    policy["age_band"] = payload.age_band
    policy["allowed_surfaces"] = payload.allowed_surfaces
    reading["allow_upload"] = payload.allow_upload
    reading["extensions"] = payload.extensions
    try:
        grant = normalize_grant(learner_user_id, grant)
        validate_grant(grant)
        _validate_reading_policy(grant)
        grant = save_grant(learner_user_id, grant)''',
    '''    policy = grant.get("learning_policy")
    if not isinstance(policy, dict):
        raise HTTPException(status_code=409, detail="Learner account has no learning policy")
    policy["age_band"] = payload.age_band
    policy["allowed_surfaces"] = payload.allowed_surfaces
    try:
        grant = normalize_grant(learner_user_id, grant)
        validate_grant(grant)
        grant = save_grant(learner_user_id, grant)''',
)

# ── book-permission endpoints ──
src = cut(
    src,
    '''@router.get("/users/{user_id}/book-permission")''',
    '''@router.post("/admin/skills/install")''',
)

# ── skills install endpoint ──
src = cut(
    src,
    '''@router.post("/admin/skills/install")''',
    '''@router.get("/users")''',
)

with io.open(path, "w", encoding="utf-8", newline="") as f:
    f.write(src)
py_compile.compile(path, doraise=True)
print("multi_user.py OK, lines:", src.count(chr(10)))
