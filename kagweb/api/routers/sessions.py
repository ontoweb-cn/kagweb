"""
Unified session history API.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from kagweb.services.session import get_session_store, get_sqlite_session_store
from kagweb.services.session.organization import (
    list_all_sessions_snapshot,
    validate_parent_assignment,
)
from kagweb.services.session.provider_response_state import (
    redact_private_message_metadata as _redact_provider_state_metadata,
)
from kagweb.services.storage.attachment_store import get_attachment_store

logger = logging.getLogger(__name__)

router = APIRouter()


class SessionRenameRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=100)


class BranchSelectionRequest(BaseModel):
    """Edit-branch picker state: `{parent_message_id: chosen_child_id}`.

    Stored inside the session preferences blob so it survives reloads
    without a dedicated column.
    """

    selected_branches: dict[str, int] = Field(default_factory=dict)


class SessionOrganizationRequest(BaseModel):
    """User-controlled organization metadata stored with the conversation."""

    course_id: str | None = None
    parent_session_id: str | None = None
    session_kind: Literal["chat", "selection_tutor"] | None = None
    pinned: bool | None = None
    archived: bool | None = None


@router.get("")
async def list_sessions(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    store = get_session_store()
    sessions = await store.list_sessions(limit=limit, offset=offset)
    return {"sessions": sessions}


# Cap (in characters) for a single event payload returned to the UI. RAG
# tools can attach whole KB documents to ``tool_result``/``observation``
# events; the frontend TraceSurface only needs a preview, and the LLM context
# is built from a separate content-only store, so capping here never affects
# model input.
MAX_EVENT_PAYLOAD = 1024 * 1024
_TRUNCATION_NOTICE = "\n\n[... content truncated]"
_TRUNCATABLE_EVENT_TYPES = ("tool_result", "observation")


def _redact_private_message_metadata(messages: list[dict[str, Any]]) -> None:
    """Remove provider-only state before session details cross the API."""
    _redact_provider_state_metadata(messages)


def _truncate_oversized_events(
    messages: list[dict[str, Any]], limit: int = MAX_EVENT_PAYLOAD
) -> None:
    """Cap oversized ``tool_result``/``observation`` payloads in place.

    The session store already returns each message's events as a parsed
    ``events`` list (see ``SqliteSessionStore._serialize_message``), so we
    mutate that list directly. Only the UI rendering path is affected.
    """

    def _cap(container: dict[str, Any], field: str) -> bool:
        value = container.get(field)
        if isinstance(value, str) and len(value) > limit:
            container[field] = value[:limit] + _TRUNCATION_NOTICE
            return True
        return False

    for msg in messages:
        events = msg.get("events")
        if not isinstance(events, list):
            continue
        for event in events:
            if not isinstance(event, dict) or event.get("type") not in _TRUNCATABLE_EVENT_TYPES:
                continue
            truncated = _cap(event, "content")
            tool_metadata = (event.get("metadata") or {}).get("tool_metadata")
            if isinstance(tool_metadata, dict):
                for field in ("content", "answer"):
                    truncated = _cap(tool_metadata, field) or truncated
            if truncated:
                event["_truncated"] = True


@router.get("/{session_id}")
async def get_session(session_id: str):
    store = get_session_store()
    session = await store.get_session_with_messages(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    _redact_private_message_metadata(session.get("messages", []))
    _truncate_oversized_events(session.get("messages", []))
    return session


@router.get("/{session_id}/trace")
async def get_session_trace(
    session_id: str,
    response_format: str = Query(
        "dsl",
        alias="format",
        pattern="^(dsl|mermaid)$",
        description="dsl → JSON document; mermaid → flowchart source",
    ),
    stable: bool = Query(
        False, description="Omit volatile fields (exported_at, …) for diff baselines"
    ),
    normalize_ids: bool = Query(
        False, description="Renumber node ids sequentially for cross-run diffs"
    ),
    include_text: bool = Query(True, description="Include user/assistant text previews"),
):
    """Session reasoning trace as a DSL document or Mermaid diagram (module 18).

    The DSL serializer is whitelist-only (#14): raw event metadata never
    leaves the server. ``format=mermaid`` returns ``text/plain`` so the
    diagram source is never rendered as HTML by a client (#78).
    """
    from fastapi.responses import PlainTextResponse

    from kagweb.services.session.dsl_export import build_session_dsl, dsl_to_mermaid

    store = get_session_store()
    session = await store.get_session_with_messages(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    doc = build_session_dsl(
        session.get("messages", []),
        stable=stable,
        normalize_ids=normalize_ids,
        include_text=include_text,
        session_id=session_id,
    )
    if response_format == "mermaid":
        return PlainTextResponse(dsl_to_mermaid(doc), media_type="text/plain; charset=utf-8")
    return doc


@router.patch("/{session_id}")
async def rename_session(session_id: str, payload: SessionRenameRequest):
    store = get_session_store()
    updated = await store.update_session_title(session_id, payload.title)
    if not updated:
        raise HTTPException(status_code=404, detail="Session not found")
    session = await store.get_session(session_id)
    return {"session": session}


@router.patch("/{session_id}/organization")
async def update_session_organization(session_id: str, payload: SessionOrganizationRequest):
    store = get_session_store()
    session = await store.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    updates: dict[str, Any] = {}
    fields = payload.model_fields_set
    if "parent_session_id" in fields:
        parent_id = str(payload.parent_session_id or "").strip()
        if parent_id == session_id:
            raise HTTPException(status_code=400, detail="A session cannot be its own parent")
        if parent_id:
            try:
                await validate_parent_assignment(
                    store,
                    session_id=session_id,
                    parent_session_id=parent_id,
                )
            except LookupError as exc:
                raise HTTPException(status_code=404, detail="Parent session not found") from exc
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        updates["parent_session_id"] = parent_id
    if "session_kind" in fields:
        updates["session_kind"] = payload.session_kind or "chat"
    if "pinned" in fields:
        updates["pinned"] = bool(payload.pinned)
    if "archived" in fields:
        updates["archived"] = bool(payload.archived)

    if updates:
        await store.update_session_preferences(session_id, updates)
        cascade_updates = {key: updates[key] for key in ("archived",) if key in updates}
        if cascade_updates:
            # Selected-text tutor threads stay with their source conversation.
            candidates = await list_all_sessions_snapshot(store)
            for candidate in candidates:
                prefs = candidate.get("preferences") or {}
                if str(prefs.get("parent_session_id") or "") == session_id:
                    await store.update_session_preferences(candidate["session_id"], cascade_updates)
    refreshed = await store.get_session(session_id)
    return {"session": refreshed}


@router.delete("/{session_id}")
async def delete_session(session_id: str):
    store = get_session_store()
    list_active_turns = getattr(store, "list_active_turns", None)
    if callable(list_active_turns):
        from kagweb.services.session import get_turn_runtime_manager

        runtime = get_turn_runtime_manager()
        for turn in await list_active_turns(session_id):
            await runtime.cancel_turn(turn["id"])
    deleted = await store.delete_session(session_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Session not found")
    try:
        await get_attachment_store().delete_session(session_id)
    except Exception:
        logger.exception("failed to clean up attachments for session %s", session_id)
    return {"deleted": True, "session_id": session_id}


@router.put("/{session_id}/branch-selection")
async def update_branch_selection(session_id: str, payload: BranchSelectionRequest):
    store = get_sqlite_session_store()
    session = await store.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    updated = await store.update_session_preferences(
        session_id, {"selected_branches": dict(payload.selected_branches)}
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"selected_branches": payload.selected_branches}


@router.delete("/{session_id}/messages/{message_id}")
async def delete_turn_by_message(session_id: str, message_id: int):
    store = get_sqlite_session_store()
    result = await store.delete_turn_by_message(session_id, message_id)
    if result["was_running"]:
        raise HTTPException(
            status_code=409, detail="Cannot delete a message while its turn is running"
        )
    if not result["deleted"]:
        raise HTTPException(status_code=404, detail="Message not found")
    attachment_store = get_attachment_store()
    for aid in result["attachment_ids"]:
        try:
            await attachment_store.delete_attachment(session_id, aid)
        except Exception:
            logger.exception("failed to delete attachment %s for session %s", aid, session_id)
    return result
