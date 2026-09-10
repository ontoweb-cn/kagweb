"""Admin APIs for the optional multi-user layer."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

from kagweb.api.routers.auth import require_admin, require_auth
from kagweb.multi_user.audit import log_admin_action, log_guardian_action
from kagweb.multi_user.context import get_current_user
from kagweb.multi_user.device_credentials import revoke_device_credentials_for_user
from kagweb.multi_user.grants import (
    LEARNING_AGE_BANDS,
    LEARNING_SURFACES,
    learner_grant,
    load_grant,
    normalize_grant,
    save_grant,
    validate_grant,
)
from kagweb.multi_user.guardians import (
    GUARDIAN_PERMISSIONS,
    authorize_guardian,
    guardian_can_access,
    list_relationships,
    relationship_by_id,
    revoke_guardian,
)
from kagweb.multi_user.identity import (
    get_user_by_id,
    list_user_info,
    set_password,
)
from kagweb.multi_user.model_access import is_owner_bound
from kagweb.multi_user.paths import (
    get_admin_path_service,
)
from kagweb.services.auth import POCKETBASE_ENABLED, hash_password
from kagweb.services.config.model_catalog import ModelCatalogService

router = APIRouter()


class GrantPayload(BaseModel):
    grant: dict[str, Any]


class GuardianAuthorizationPayload(BaseModel):
    guardian_user_id: str = Field(min_length=1, max_length=64)
    learner_user_id: str = Field(min_length=1, max_length=64)
    permissions: list[str] = Field(default_factory=lambda: sorted(GUARDIAN_PERMISSIONS))

    @field_validator("permissions")
    @classmethod
    def permissions_valid(cls, value: list[str]) -> list[str]:
        permissions = sorted(set(value))
        if not permissions or not set(permissions).issubset(GUARDIAN_PERMISSIONS):
            raise ValueError("Unknown guardian permission")
        return permissions


class GuardianRestrictionsPayload(BaseModel):
    age_band: str
    allowed_surfaces: list[str]

    @field_validator("age_band")
    @classmethod
    def age_band_valid(cls, value: str) -> str:
        if value not in LEARNING_AGE_BANDS:
            raise ValueError("Unknown learning age band")
        return value

    @field_validator("allowed_surfaces")
    @classmethod
    def surfaces_valid(cls, value: list[str]) -> list[str]:
        surfaces = list(dict.fromkeys(value))
        if not surfaces or not set(surfaces).issubset(LEARNING_SURFACES):
            raise ValueError("Unknown or empty learning surface")
        return surfaces


class GuardianCredentialResetPayload(BaseModel):
    new_password: str = Field(min_length=8, max_length=128)


def _admin_catalog_summary() -> dict[str, list[dict[str, Any]]]:
    catalog = ModelCatalogService(
        path=get_admin_path_service().get_settings_file("model_catalog")
    ).load()
    out: dict[str, list[dict[str, Any]]] = {"llm": []}
    for service, state in (catalog.get("services") or {}).items():
        if service not in out:
            continue
        for profile in state.get("profiles", []) or []:
            if is_owner_bound(profile):
                # Bound to one person's OAuth identity, so it is not assignable.
                # Listing it here would offer admins a grant the server drops.
                continue
            profile_id = str(profile.get("id") or "")
            models = []
            for model in profile.get("models", []) or []:
                models.append(
                    {
                        "model_id": model.get("id", ""),
                        "name": model.get("name") or model.get("model") or model.get("id"),
                        "model": model.get("model", ""),
                    }
                )
            out[service].append(
                {
                    "profile_id": profile_id,
                    "name": profile.get("name") or profile_id,
                    "models": models,
                }
            )
    return out


def _admin_partner_summary() -> list[dict[str, Any]]:
    """The partners an admin can hand to someone else.

    Admin-managed partners only — the ones with no owner, or that the admin
    created. A partner someone built for themselves is theirs to share or not;
    listing it here would let an admin lend out a private companion (and its
    soul, which people write personally) by a single click. Identity only: no
    channel wiring or model selection leaks into the assignable summary.
    """
    from kagweb.services.partners import get_partner_manager

    admin_id = get_current_user().id
    return [
        {
            "partner_id": str(item.get("partner_id") or ""),
            "name": item.get("name") or item.get("partner_id") or "",
            "description": item.get("description") or "",
            "emoji": item.get("emoji") or "",
        }
        for item in get_partner_manager().list_partners()
        if str(item.get("owner_id") or "") in ("", admin_id)
    ]


def _require_assignable_user(user_id: str) -> tuple[str, dict[str, Any]]:
    user_record = get_user_by_id(user_id)
    if user_record is None:
        raise HTTPException(status_code=404, detail="User not found")
    username, record = user_record
    if str(record.get("role") or "user") == "admin":
        raise HTTPException(
            status_code=403,
            detail="Admin users use the main workspace and cannot receive assignments.",
        )
    return username, record


def _users_by_id() -> dict[str, dict[str, Any]]:
    return {str(user.get("id") or ""): user for user in list_user_info()}


def _relationship_view(
    relationship: dict[str, Any], users: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    guardian = users.get(relationship["guardian_user_id"], {})
    learner = users.get(relationship["learner_user_id"], {})
    return {
        **relationship,
        "guardian_username": str(guardian.get("username") or ""),
        "learner_username": str(learner.get("username") or ""),
    }


def _require_guardian_access(
    current: object, learner_user_id: str, permission: str
) -> tuple[str, dict[str, Any], str, bool]:
    actor_user_id = str(getattr(current, "user_id", "") or "")
    learner_username, learner_record = _require_assignable_user(learner_user_id)
    is_admin = str(getattr(current, "role", "") or "") == "admin"
    if is_admin:
        return learner_username, learner_record, actor_user_id, True
    _guardian_username, _guardian_record = _require_assignable_user(actor_user_id)
    if not guardian_can_access(actor_user_id, learner_user_id, permission):
        raise HTTPException(status_code=403, detail="Guardian authorization required")
    return learner_username, learner_record, actor_user_id, False


def _log_supervisor_action(
    action: str,
    *,
    actor_user_id: str,
    learner_user_id: str,
    is_admin: bool,
    summary: dict[str, Any] | None = None,
) -> None:
    if is_admin:
        log_admin_action(action, target_user_id=learner_user_id, summary=summary)
        return
    log_guardian_action(
        action,
        guardian_user_id=actor_user_id,
        learner_user_id=learner_user_id,
        summary=summary,
    )


@router.get("/admin/resources")
async def admin_resources(_: object = Depends(require_admin)) -> dict[str, Any]:
    """Everything an admin can assign to a user: models and
    the tool surface (system tools + MCP tools, same pool partners use)."""
    from kagweb.api.utils.tool_options import build_tool_options

    tool_options = await build_tool_options()
    return {
        "models": _admin_catalog_summary(),
        "partners": _admin_partner_summary(),
    }


@router.get("/guardians")
async def list_guardian_relationships(
    include_revoked: bool = False,
    _: object = Depends(require_admin),
) -> dict[str, Any]:
    users = _users_by_id()
    relationships = [
        _relationship_view(relationship, users)
        for relationship in list_relationships(include_revoked=include_revoked)
    ]
    return {"relationships": relationships}


@router.post("/guardians", status_code=201)
async def authorize_guardian_relationship(
    payload: GuardianAuthorizationPayload,
    current: object = Depends(require_admin),
) -> dict[str, Any]:
    _require_assignable_user(payload.guardian_user_id)
    _require_assignable_user(payload.learner_user_id)
    try:
        relationship = authorize_guardian(
            payload.guardian_user_id,
            payload.learner_user_id,
            payload.permissions,
        )
    except ValueError as exc:
        message = str(exc)
        status_code = 409 if "already authorized" in message else 400
        raise HTTPException(status_code=status_code, detail=message) from exc
    log_admin_action(
        "guardian_authorize",
        target_user_id=payload.learner_user_id,
        summary={
            "relationship_id": relationship["id"],
            "guardian_user_id": payload.guardian_user_id,
            "permissions": relationship["permissions"],
        },
    )
    return {
        "relationship": _relationship_view(relationship, _users_by_id()),
        "actor_username": str(getattr(current, "username", "") or ""),
    }


@router.delete("/guardians/{relationship_id}")
async def revoke_guardian_relationship(
    relationship_id: str,
    current: object = Depends(require_admin),
) -> dict[str, Any]:
    revoked_by = str(getattr(current, "user_id", "") or "")
    relationship = revoke_guardian(relationship_id, revoked_by=revoked_by)
    if relationship is None:
        raise HTTPException(status_code=404, detail="Guardian relationship not found")
    log_admin_action(
        "guardian_revoke",
        target_user_id=relationship["learner_user_id"],
        summary={
            "relationship_id": relationship["id"],
            "guardian_user_id": relationship["guardian_user_id"],
        },
    )
    return {
        "relationship": _relationship_view(relationship, _users_by_id()),
        "ok": True,
    }


@router.get("/me/guardianships")
async def my_guardianships(
    current: object = Depends(require_auth),
) -> dict[str, Any]:
    guardian_user_id = str(getattr(current, "user_id", "") or "")
    _require_assignable_user(guardian_user_id)
    users = _users_by_id()
    relationships = [
        _relationship_view(relationship, users)
        for relationship in list_relationships(guardian_user_id=guardian_user_id)
    ]
    return {"relationships": relationships}


@router.delete("/me/guardianships/{relationship_id}")
async def revoke_my_guardianship(
    relationship_id: str,
    current: object = Depends(require_auth),
) -> dict[str, Any]:
    guardian_user_id = str(getattr(current, "user_id", "") or "")
    _require_assignable_user(guardian_user_id)
    relationship = relationship_by_id(relationship_id)
    if (
        relationship is None
        or relationship["guardian_user_id"] != guardian_user_id
        or relationship["revoked_at"] is not None
    ):
        raise HTTPException(status_code=404, detail="Active guardian relationship not found")
    revoked = revoke_guardian(relationship_id, revoked_by=guardian_user_id, reason="self_revoked")
    assert revoked is not None
    log_guardian_action(
        "guardian_self_revoke",
        guardian_user_id=guardian_user_id,
        learner_user_id=relationship["learner_user_id"],
        summary={"relationship_id": relationship_id},
    )
    return {"relationship": _relationship_view(revoked, _users_by_id()), "ok": True}


def _guardian_restrictions(grant: dict[str, Any]) -> dict[str, Any]:
    policy = grant.get("learning_policy")
    if not isinstance(policy, dict):
        raise HTTPException(status_code=409, detail="Learner account has no learning policy")
    return {
        "age_band": policy.get("age_band"),
        "allowed_surfaces": [
            s for s in (policy.get("allowed_surfaces") or ["chat"]) if s != "reading"
        ],
    }


def _restriction_grant(learner_user_id: str, learner_record: dict[str, Any]) -> dict[str, Any]:
    grant = load_grant(learner_user_id)
    if grant.get("learning_policy") is None and learner_record.get("preset") == "learner":
        return learner_grant(learner_user_id)
    return grant


@router.get("/learners/{learner_user_id}/restrictions")
async def get_guardian_restrictions(
    learner_user_id: str,
    current: object = Depends(require_auth),
) -> dict[str, Any]:
    _learner_username, learner_record, actor_user_id, is_admin = _require_guardian_access(
        current, learner_user_id, "manage_restrictions"
    )
    restrictions = _guardian_restrictions(_restriction_grant(learner_user_id, learner_record))
    _log_supervisor_action(
        "guardian_restrictions_view",
        actor_user_id=actor_user_id,
        learner_user_id=learner_user_id,
        is_admin=is_admin,
    )
    return {"restrictions": restrictions}


@router.put("/learners/{learner_user_id}/restrictions")
async def put_guardian_restrictions(
    learner_user_id: str,
    payload: GuardianRestrictionsPayload,
    current: object = Depends(require_auth),
) -> dict[str, Any]:
    _learner_username, learner_record, actor_user_id, is_admin = _require_guardian_access(
        current, learner_user_id, "manage_restrictions"
    )
    grant = deepcopy(_restriction_grant(learner_user_id, learner_record))
    policy = grant.get("learning_policy")
    if not isinstance(policy, dict):
        raise HTTPException(status_code=409, detail="Learner account has no learning policy")
    policy["age_band"] = payload.age_band
    policy["allowed_surfaces"] = payload.allowed_surfaces
    try:
        grant = normalize_grant(learner_user_id, grant)
        validate_grant(grant)
        grant = save_grant(learner_user_id, grant)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    restrictions = _guardian_restrictions(grant)
    _log_supervisor_action(
        "guardian_restrictions_set",
        actor_user_id=actor_user_id,
        learner_user_id=learner_user_id,
        is_admin=is_admin,
        summary=restrictions,
    )
    return {"restrictions": restrictions}


@router.post("/learners/{learner_user_id}/credentials/reset")
async def reset_learner_credentials(
    learner_user_id: str,
    payload: GuardianCredentialResetPayload,
    current: object = Depends(require_auth),
) -> dict[str, Any]:
    learner_username, _learner_record, actor_user_id, is_admin = _require_guardian_access(
        current, learner_user_id, "reset_credentials"
    )
    if POCKETBASE_ENABLED:
        raise HTTPException(
            status_code=400,
            detail="Guardian credential reset requires built-in local authentication.",
        )
    revoked_devices = revoke_device_credentials_for_user(
        learner_user_id,
        revoked_by=actor_user_id,
    )
    if set_password(learner_username, hash_password(payload.new_password)) is None:
        raise HTTPException(status_code=404, detail="User not found")
    _log_supervisor_action(
        "guardian_credential_reset",
        actor_user_id=actor_user_id,
        learner_user_id=learner_user_id,
        is_admin=is_admin,
        summary={
            "credential_reset": True,
            "device_credentials_revoked": revoked_devices,
        },
    )
    return {"ok": True, "device_credentials_revoked": revoked_devices}


@router.get("/users/{user_id}/grants")
async def get_user_grants(user_id: str, _: object = Depends(require_admin)) -> dict[str, Any]:
    _require_assignable_user(user_id)
    return {"grant": load_grant(user_id)}


@router.put("/users/{user_id}/grants")
async def put_user_grants(
    user_id: str,
    payload: GrantPayload,
    _: object = Depends(require_admin),
) -> dict[str, Any]:
    user_record = _require_assignable_user(user_id)
    try:
        grant = normalize_grant(user_id, payload.grant)
        if (
            str(user_record[1].get("preset") or "standard") == "learner"
            and grant.get("learning_policy") is None
        ):
            raise ValueError("Learner accounts must retain a learning policy.")
        validate_grant(grant)
        grant = save_grant(user_id, grant)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    log_admin_action(
        "grant_set",
        target_user_id=user_id,
        summary={
            "model_count": len(grant.get("models", {}).get("llm", []) or []),
            "partner_count": len(grant.get("partners", []) or []),
            "enabled_tools": grant.get("enabled_tools"),
            "mcp_tool_count": (
                None if grant.get("mcp_tools") is None else len(grant.get("mcp_tools") or [])
            ),
            "exec_enabled": grant.get("exec_enabled"),
            "learning_policy": grant.get("learning_policy"),
        },
    )
    return {"grant": grant}


@router.get("/users")
async def multi_user_list_users(_: object = Depends(require_admin)) -> dict[str, Any]:
    return {"users": list_user_info()}
