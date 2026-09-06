"""One-shot Phase 1 patch: rewrite request_preparer.py (trimmed). Run from repo root."""
import io
import py_compile

path = "deepmentor/services/session/turns/request_preparer.py"
with io.open(path, "r", encoding="utf-8") as f:
    src = f.read()


def rep(src, old, new, count=1):
    assert old in src, f"NOT FOUND: {old[:90]!r}"
    return src.replace(old, new, count)


def cut(src, start, end):
    i = src.index(start)
    j = src.index(end, i)
    return src[:i] + src[j:]


# ── imports ──
src = rep(
    src,
    """from deepmentor.core.stream import StreamEvent, StreamEventType
from deepmentor.core.turn_request import TurnRequest
from deepmentor.runtime.capability_routing import route_explicit_quiz_request
from deepmentor.services.session.workspace_preferences import (
    WORKSPACE_MODE_MASTERY,
    WORKSPACE_MODE_READING,
)

from .._turn_runtime_shared import (
    _apply_course_defaults,
    _coerce_bool,
    _course_conventions_block,
    _extract_selection_tutor_context,
    _llm_selection_dict,
    _mastery_loop_managed,
    _mastery_path_id,
    _partner_group_references,
    _reading_material_id,
    _reading_material_revision,
    _reading_references,
    _reading_workspace_id,
    _resolve_selection_tutor_context,
    _timed_media_id,
    _TurnExecution,
    _workspace_mode,
)""",
    """from deepmentor.core.stream import StreamEvent, StreamEventType
from deepmentor.core.turn_request import TurnRequest

from .._turn_runtime_shared import (
    _coerce_bool,
    _extract_selection_tutor_context,
    _llm_selection_dict,
    _partner_group_references,
    _resolve_selection_tutor_context,
    _TurnExecution,
)""",
)

# ── TYPE_CHECKING decls: drop mastery validators ──
src = rep(
    src,
    """        async def _validate_mastery_session_topic(
            self,
            *,
            session_id: str,
            requested_path_id: str,
            remembered_path_id: str,
        ) -> None: ...

        async def _acquire_mastery_path_lease(
            self,
            *,
            path_id: str,
            session_id: str,
            turn_id: str,
            owns_path: bool,
        ) -> None: ...

""",
    "",
)

# ── start_turn: course binding block ──
src = rep(
    src,
    """        course_id_explicit = "course_id" in payload
        requested_course_id = str(
            (payload.get("course_id") if course_id_explicit else preferences.get("course_id")) or ""
        ).strip()
        bound_course = None
        if requested_course_id:
            from deepmentor.services.courses import (
                CourseNotFoundError,
                get_course_service,
            )

            try:
                bound_course = await asyncio.to_thread(
                    get_course_service().get,
                    requested_course_id,
                )
            except CourseNotFoundError:
                requested_course_id = ""
        if bound_course is not None:
            payload = _apply_course_defaults(
                payload,
                bound_course,
                preferences=preferences,
            )

        requested_capability = str(payload.get("capability") or "chat")
        capability_route = route_explicit_quiz_request(
            payload.get("content"),
            requested_capability,
            enabled=routing_enabled,
        )
        capability = (
            capability_route.capability if capability_route is not None else requested_capability
        )
        try:
            from deepmentor.multi_user.learning_access import apply_learning_policy

            payload = apply_learning_policy({**payload, "capability": capability})
        except PermissionError as exc:
            raise RuntimeError(str(exc)) from exc

        workspace_mode_explicit = "workspace_mode" in payload""",
    """        requested_capability = str(payload.get("capability") or "chat")
        capability = requested_capability

        workspace_mode_explicit = "workspace_mode" in payload""",
)

# ── config validation: no routing variance ──
src = rep(
    src,
    """            # A routed capability owns a different schema. Chat-only options
            # must not be smuggled into the generator; the user request itself
            # carries the desired topic/count.
            validated_public_config = validate_capability_config(
                capability,
                {} if capability_route is not None else raw_config,
            )""",
    """            validated_public_config = validate_capability_config(capability, raw_config)""",
)

# ── payload assembly: drop course/reading/mastery ──
src = rep(
    src,
    """        payload = {
            **payload,
            "capability": capability,
            "workspace_mode": workspace_mode,
            "course_id": requested_course_id,
            # Rendered here, where the course is already loaded and validated,
            # and carried on the payload so the run phase needs no second read.
            # Session preferences are assembled key by key below, so this rides
            # along without being persisted.
            "course_conventions": (
                _course_conventions_block(bound_course, str(payload.get("language") or "en"))
                if bound_course is not None
                else ""
            ),
            "requested_capability": requested_capability,
            "capability_route": (
                capability_route.as_metadata() if capability_route is not None else None
            ),
            "config": validated_public_config,
        }
        reading_workspace_id = _reading_workspace_id(payload.get("reading_workspace_id"))
        reading_material_id = _reading_material_id(payload.get("reading_material_id"))
        reading_material_revision = _reading_material_revision(
            payload.get("reading_material_revision")
        )
        if workspace_mode == WORKSPACE_MODE_READING and reading_workspace_id:
            from deepmentor.reading import ReadingCatalogStore

            reading_catalog = ReadingCatalogStore()
            reading_workspace = reading_catalog.get_workspace(reading_workspace_id)
            if reading_workspace is None:
                raise RuntimeError("The reading workspace is unavailable.")
            reading_material_id = reading_material_id or str(
                reading_workspace.active_material_id or ""
            )
            if reading_material_id and reading_material_id not in {
                tab.material.material_id for tab in reading_workspace.tabs
            }:
                raise RuntimeError("The active material is not part of this reading workspace.")
            reading_catalog.attach_session(
                reading_workspace_id,
                session["id"],
                title=str(session.get("title") or "New reading conversation"),
                active_material_id=reading_material_id or None,
            )
            payload = {
                **payload,
                "reading_workspace_id": reading_workspace_id,
                "reading_material_id": reading_material_id,
                "reading_material_revision": reading_material_revision,
            }
        # A mastery path has a longer lifetime than any one conversation.
        # Persist the explicit association on the session, and restore it on
        # later turns whose frontend payload omits the field.
        mastery_path_explicit = "mastery_path_id" in payload
        configured_mastery_path_id = _mastery_path_id(
            payload.get("mastery_path_id")
            if mastery_path_explicit
            else preferences.get("mastery_path_id")
        )
        mastery_binding = None
        if workspace_mode == WORKSPACE_MODE_MASTERY:
            from deepmentor.learning.identity import resolve_mastery_path_binding

            mastery_binding = resolve_mastery_path_binding(
                configured_path_id=configured_mastery_path_id,
                book_references=payload.get("book_references", []),
                session_id=session["id"],
            )
            mastery_path_id = mastery_binding.path_id
            await self._validate_mastery_session_topic(
                session_id=session["id"],
                requested_path_id=mastery_path_id,
                remembered_path_id=_mastery_path_id(preferences.get("mastery_path_id")),
            )
        else:
            mastery_path_id = configured_mastery_path_id
        mastery_lease_managed = bool(
            mastery_binding is not None and _mastery_loop_managed(workspace_mode, capability)
        )
        payload = {
            **payload,
            "mastery_path_id": mastery_path_id,
            "mastery_path_lease_managed": mastery_lease_managed,
        }
        # Persona is a session-level preference (mirrors llm_selection): an""",
    """        payload = {
            **payload,
            "capability": capability,
            "workspace_mode": workspace_mode,
            "requested_capability": requested_capability,
            "config": validated_public_config,
        }
        # Persona is a session-level preference (mirrors llm_selection): an""",
)

# ── persona comment referencing course ──
src = rep(
    src,
    """        # Persona is a session-level preference (mirrors llm_selection): an
        # explicit ``persona`` key in the payload — including an empty string,
        # which means "Default" / no persona — wins and is persisted below.
        # A course default is filled above only when that key was absent; with
        # neither, the session's stored preference survives reloads.""",
    """        # Persona is a session-level preference (mirrors llm_selection): an
        # explicit ``persona`` key in the payload — including an empty string,
        # which means "Default" / no persona — wins and is persisted below.
        # Without one, the session's stored preference survives reloads.""",
)

# ── tool whitelist: drop routing-based filter ──
src = rep(
    src,
    """        if capability_route is not None and capability_route.auto_routed:
            from deepmentor.runtime.registry.capability_registry import (
                get_capability_registry,
            )

            routed_capability = get_capability_registry().get(capability_route.capability)
            if routed_capability is not None:
                allowed_by_manifest = set(routed_capability.manifest.tools_used)
                payload = {
                    **payload,
                    "tools": [
                        tool for tool in (payload.get("tools") or []) if tool in allowed_by_manifest
                    ],
                }
        payload = {**payload, "llm_selection": llm_selection}""",
    """        payload = {**payload, "llm_selection": llm_selection}""",
)

# ── preference update ──
src = rep(
    src,
    """        preference_update: dict[str, Any] = {
            # Auto-routing is a one-turn execution choice; keep the durable
            # preference on what the caller explicitly selected.
            "capability": requested_capability,
            "tools": list(payload.get("tools") or []),
            "knowledge_bases": list(payload.get("knowledge_bases") or []),
            "language": str(payload.get("language") or "en"),
        }
        # Missing legacy chat fields should not manufacture an empty stored
        # preference. Explicit empties still clear a workspace, while a
        # non-empty legacy capability is persisted as part of migration.
        if workspace_mode_explicit or workspace_mode:
            preference_update["workspace_mode"] = workspace_mode
        if course_id_explicit:
            preference_update["course_id"] = requested_course_id
""",
    """        preference_update: dict[str, Any] = {
            "capability": requested_capability,
            "tools": list(payload.get("tools") or []),
            "language": str(payload.get("language") or "en"),
        }
        # Missing legacy chat fields should not manufacture an empty stored
        # preference. Explicit empties still clear a workspace, while a
        # non-empty legacy capability is persisted as part of migration.
        if workspace_mode_explicit or workspace_mode:
            preference_update["workspace_mode"] = workspace_mode
""",
)

# ── mastery preference persist + reading preference persist ──
src = rep(
    src,
    """        if mastery_path_explicit or mastery_binding is not None:
            # Mastery turns persist their fully resolved path so a later turn
            # cannot silently fall back to a different aggregate.
            preference_update["mastery_path_id"] = mastery_path_id
        if workspace_mode == WORKSPACE_MODE_READING and reading_workspace_id:
            preference_update.update(
                {
                    "session_kind": "immersive_reading",
                    "reading_workspace_id": reading_workspace_id,
                    "reading_material_id": reading_material_id,
                }
            )
        elif (
            workspace_mode_explicit
            and not workspace_mode
            and preferences.get("session_kind") == "immersive_reading"
        ):
            preference_update.update(
                {
                    "session_kind": "chat",
                    "reading_workspace_id": "",
                    "reading_material_id": "",
                }
            )
        await self.store.update_session_preferences(session["id"], preference_update)""",
    """        await self.store.update_session_preferences(session["id"], preference_update)""",
)

# ── brand string (Phase 3 will do global, but fix message text now) ──
src = rep(
    src,
    '''                    error="DeepMentor is preparing an update; try again after it reconnects",''',
    '''                    error="The server is preparing an update; try again after it reconnects",''',
)
src = rep(
    src,
    '''            raise RuntimeError("DeepMentor is preparing an update; try again after it reconnects")''',
    '''            raise RuntimeError("The server is preparing an update; try again after it reconnects")''',
)

# ── mastery lease acquisition after turn row ──
src = rep(
    src,
    """        mastery_lease_acquired = False
        if mastery_binding is not None and mastery_lease_managed:
            try:
                await self._acquire_mastery_path_lease(
                    path_id=mastery_binding.path_id,
                    session_id=session["id"],
                    turn_id=turn["id"],
                    owns_path=mastery_binding.owned_by_session,
                )
                mastery_lease_acquired = True
            except Exception as exc:
                async with self._lock:
                    self._executions.pop(turn["id"], None)
                with contextlib.suppress(Exception):
                    await self.store.transition_turn(
                        turn["id"],
                        "failed",
                        error=str(exc),
                        failure_code="rejected",
                    )
                raise
            persisted_turn = await self.store.get_turn(turn["id"])
            if persisted_turn is None or persisted_turn.get("status") != "running":
                # An administrative reset/delete can cancel the placeholder
                # while lease acquisition is in flight. Never launch a task
                # after that cancellation has already become durable.
                from deepmentor.learning.storage import LearningStore

                async with self._lock:
                    self._executions.pop(turn["id"], None)
                with contextlib.suppress(Exception):
                    await asyncio.to_thread(
                        LearningStore().release_path_lease,
                        mastery_binding.path_id,
                        turn_id=turn["id"],
                    )
                raise RuntimeError("Mastery turn was cancelled while starting")
        session_metadata: dict[str, Any] = {""",
    """        session_metadata: dict[str, Any] = {""",
)

src = rep(
    src,
    """        if capability_route is not None:
            session_metadata["capability_route"] = capability_route.as_metadata()
        try:""",
    """        try:""",
)

# ── launch failure: mastery lease release ──
src = rep(
    src,
    """        except Exception as exc:
            async with self._lock:
                self._executions.pop(turn["id"], None)
            if mastery_binding is not None and mastery_lease_acquired:
                from deepmentor.learning.storage import LearningStore

                with contextlib.suppress(Exception):
                    await asyncio.to_thread(
                        LearningStore().release_path_lease,
                        mastery_binding.path_id,
                        turn_id=turn["id"],
                    )
            with contextlib.suppress(Exception):
                await self.store.update_turn_status(turn["id"], "failed", str(exc))""",
    """        except Exception as exc:
            async with self._lock:
                self._executions.pop(turn["id"], None)
            with contextlib.suppress(Exception):
                await self.store.update_turn_status(turn["id"], "failed", str(exc))""",
)

# ── regenerate_last_turn payload ──
src = rep(
    src,
    """        capability = str(
            overrides.get("capability")
            or last_user.get("capability")
            or preferences.get("capability")
            or "chat"
        )
        tools = list(
            overrides.get("tools")
            if overrides.get("tools") is not None
            else preferences.get("tools") or []
        )
        knowledge_bases = list(
            overrides.get("knowledge_bases")
            if overrides.get("knowledge_bases") is not None
            else preferences.get("knowledge_bases") or []
        )
        language = str(overrides.get("language") or preferences.get("language") or "en")

        config: dict[str, Any] = dict(overrides.get("config") or {})
        llm_selection = (
            overrides.get("llm_selection")
            if overrides.get("llm_selection") is not None
            else snapshot.get("llmSelection") or preferences.get("llm_selection")
        )
        mastery_path_id = _mastery_path_id(
            overrides.get("mastery_path_id")
            if "mastery_path_id" in overrides
            else snapshot.get("masteryPathId") or preferences.get("mastery_path_id")
        )
        workspace_mode = _workspace_mode(
            overrides.get("workspace_mode")
            if "workspace_mode" in overrides
            else snapshot.get("workspaceMode") or preferences.get("workspace_mode"),
            capability=capability,
        )

        payload: dict[str, Any] = {
            "session_id": session_id,
            "capability": capability,
            "workspace_mode": workspace_mode,
            "content": str(last_user.get("content", "") or ""),
            "tools": tools,
            "knowledge_bases": knowledge_bases,
            "language": language,
            "attachments": list(last_user.get("attachments") or []),
            "notebook_references": list(
                overrides.get("notebook_references")
                if overrides.get("notebook_references") is not None
                else preferences.get("notebook_references") or []
            ),
            "history_references": list(
                overrides.get("history_references")
                if overrides.get("history_references") is not None
                else preferences.get("history_references") or []
            ),
            "partner_group_references": _partner_group_references(
                overrides.get("partner_group_references")
                if overrides.get("partner_group_references") is not None
                else snapshot.get("partnerGroupReferences")
                or preferences.get("partner_group_references")
                or []
            ),
            "book_references": list(
                overrides.get("book_references")
                if overrides.get("book_references") is not None
                else snapshot.get("bookReferences") or []
            ),
            "reading_references": _reading_references(
                overrides.get("reading_references")
                if "reading_references" in overrides
                else snapshot.get("readingReferences")
            ),
            "mastery_path_id": mastery_path_id,
            # Recovered from the original turn's snapshot so the regenerate runs
            # against the same document. An explicit override wins (the reader
            # may have moved on), and the viewport is deliberately not restored —
            # "where the user was looking" is stale by definition on a retry.
            "reading_material_id": _reading_material_id(
                overrides.get("reading_material_id")
                if "reading_material_id" in overrides
                else snapshot.get("readingMaterialId")
            ),
            "reading_material_revision": _reading_material_revision(
                overrides.get("reading_material_revision")
                if "reading_material_revision" in overrides
                else snapshot.get("readingMaterialRevision")
            ),
            "reading_workspace_id": _reading_workspace_id(
                overrides.get("reading_workspace_id")
                if "reading_workspace_id" in overrides
                else snapshot.get("readingWorkspaceId") or preferences.get("reading_workspace_id")
            ),
            "timed_media_id": _timed_media_id(
                overrides.get("timed_media_id")
                if "timed_media_id" in overrides
                else snapshot.get("timedMediaId")
            ),
            "config": config,
            "persist_user_message": False,
            "regenerate": True,
            "regenerated_from_message_id": int(last_user["id"]),
        }""",
    """        capability = str(
            overrides.get("capability")
            or last_user.get("capability")
            or preferences.get("capability")
            or "chat"
        )
        tools = list(
            overrides.get("tools")
            if overrides.get("tools") is not None
            else preferences.get("tools") or []
        )
        language = str(overrides.get("language") or preferences.get("language") or "en")

        config: dict[str, Any] = dict(overrides.get("config") or {})
        llm_selection = (
            overrides.get("llm_selection")
            if overrides.get("llm_selection") is not None
            else snapshot.get("llmSelection") or preferences.get("llm_selection")
        )

        payload: dict[str, Any] = {
            "session_id": session_id,
            "capability": capability,
            "content": str(last_user.get("content", "") or ""),
            "tools": tools,
            "language": language,
            "attachments": list(last_user.get("attachments") or []),
            "history_references": list(
                overrides.get("history_references")
                if overrides.get("history_references") is not None
                else preferences.get("history_references") or []
            ),
            "partner_group_references": _partner_group_references(
                overrides.get("partner_group_references")
                if overrides.get("partner_group_references") is not None
                else snapshot.get("partnerGroupReferences")
                or preferences.get("partner_group_references")
                or []
            ),
            "config": config,
            "persist_user_message": False,
            "regenerate": True,
            "regenerated_from_message_id": int(last_user["id"]),
        }""",
)

with io.open(path, "w", encoding="utf-8", newline="") as f:
    f.write(src)
py_compile.compile(path, doraise=True)
print("request_preparer OK, lines:", src.count(chr(10)))
