"""Admin APIs for the optional multi-user layer."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from kagweb.api.routers.auth import require_admin
from kagweb.multi_user.audit import log_admin_action
from kagweb.multi_user.grants import (
    load_grant,
    normalize_grant,
    save_grant,
    validate_grant,
)
from kagweb.multi_user.identity import (
    get_user_by_id,
    list_user_info,
)
from kagweb.multi_user.model_access import is_owner_bound
from kagweb.multi_user.paths import (
    get_admin_path_service,
)
from kagweb.services.config.model_catalog import ModelCatalogService

router = APIRouter()


class GrantPayload(BaseModel):
    grant: dict[str, Any]


#: First MCP connect can be slow; the admin page must not hang on it.
_MCP_CATALOG_START_TIMEOUT = 10.0


async def _admin_mcp_tool_options() -> list[dict[str, Any]]:
    """The assignable MCP tool catalog for the grants editor.

    Listed from the same process registry the runtime allowlist filters
    (``view.py``), so a name shown here is exactly a name the grant can
    whitelist. Starting the manager is best-effort: on failure the catalog is
    simply empty — the grants editor renders "nothing configured" instead of
    erroring, and a later save keeps the existing whitelist untouched.
    """
    import asyncio

    from kagweb.runtime.registry.tool_registry import get_tool_registry
    from kagweb.services.mcp import get_mcp_manager

    try:
        await asyncio.wait_for(
            get_mcp_manager().ensure_started(), timeout=_MCP_CATALOG_START_TIMEOUT
        )
    except Exception:
        return []
    tools: list[dict[str, Any]] = []
    for tool in get_tool_registry().deferred_tools():
        definition = tool.get_definition()
        tools.append(
            {
                "name": definition.name,
                "description": definition.description,
                # Grouping key for the editor (the MCP server's name).
                "provider_id": getattr(tool, "provider_id", ""),
                "kind": "mcp",
            }
        )
    return sorted(tools, key=lambda item: str(item["name"]))


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


@router.get("/admin/resources")
async def admin_resources(_: object = Depends(require_admin)) -> dict[str, Any]:
    """Everything an admin can assign to a user: model catalog + MCP tools."""
    return {"models": _admin_catalog_summary(), "mcp_tools": await _admin_mcp_tool_options()}


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
    _require_assignable_user(user_id)
    try:
        grant = normalize_grant(user_id, payload.grant)
        validate_grant(grant)
        grant = save_grant(user_id, grant)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    log_admin_action(
        "grant_set",
        target_user_id=user_id,
        summary={
            "model_count": len(grant.get("models", {}).get("llm", []) or []),
            "mcp_tool_count": (
                None if grant.get("mcp_tools") is None else len(grant.get("mcp_tools") or [])
            ),
            "exec_enabled": grant.get("exec_enabled"),
        },
    )
    return {"grant": grant}


@router.get("/users")
async def multi_user_list_users(_: object = Depends(require_admin)) -> dict[str, Any]:
    return {"users": list_user_info()}
