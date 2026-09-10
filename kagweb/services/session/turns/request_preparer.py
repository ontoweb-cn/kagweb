"""Generated behavior slice of the unified turn runtime."""

from __future__ import annotations

import asyncio
import contextlib
import time
from typing import TYPE_CHECKING, Any
import uuid

from kagweb.core.stream import StreamEvent, StreamEventType
from kagweb.core.turn_request import TurnRequest

from .._turn_runtime_shared import (
    _extract_selection_tutor_context,
    _llm_selection_dict,
    _partner_group_references,
    _resolve_selection_tutor_context,
    _TurnExecution,
)

if TYPE_CHECKING:
    from kagweb.runtime.coordination import RuntimeCoordinator
    from kagweb.services.session.protocol import SessionStoreProtocol


def _access_denied_message(required: str) -> str:
    """A refusal the person reading it can act on.

    ``agent_loop_cli`` is denied by default, and the missing grant is not
    obvious from the capability name — the admin needs to know they are
    granting code execution on this host, not a model.
    """
    if required == "agent_loop_cli":
        return (
            "This agent backend runs as a local process with the server's "
            "privileges. Your account is not allowed to start it; ask an "
            "administrator to grant agent-process access."
        )
    return f"No {required} access is assigned to your account. Please contact an administrator."


def _effective_required_service(
    capability: str, *, agent_loop_block: dict[str, Any] | None = None
) -> str:
    """The resource a turn needs, resolved against what is actually configured.

    A capability's manifest declares a static ``required_service``, but `chat`
    is the one capability whose need depends on the deployment: with an agent
    backend configured it delegates the turn and never touches KAGWeb's own LLM
    layer, while the framework-shell stub has nothing to run at all. So the
    static answer ("llm") is wrong in exactly the deployment this project ships
    for, and resolving it here — where the settings are readable — keeps the
    gate honest without making the manifest dynamic.

    **Which** backend also decides how strict the check is. The CLI and ACP
    families spawn a child process on this host with the server's privileges, so
    driving one is code execution as the server user; a user may only do that
    with an explicit grant (``agent_loop_cli``, denied by default). The HTTP
    family runs the loop in the operator's own service and starts nothing
    locally, so it keeps the deployment-wide default.

    Everything else keeps its declared service.
    """
    if capability != "chat":
        from kagweb.runtime.registry.capability_registry import get_capability_registry

        entry = get_capability_registry().get(capability)
        declared = getattr(getattr(entry, "manifest", None), "required_service", "")
        return str(declared or "llm")

    from kagweb.services.agent_loop.builtin import preset_family
    from kagweb.services.agent_loop.settings import (
        get_agent_loop_settings,
        resolve_primary_profile,
    )

    try:
        block = agent_loop_block if agent_loop_block is not None else get_agent_loop_settings()
        profile = resolve_primary_profile(block)
    except Exception:
        # An unreadable settings file must not turn into "allow": fall back to
        # the declared service and let the LLM gate decide, as before.
        return "llm"
    if profile is None:
        # No backend → the shell stub. Nothing drives the turn.
        return "llm"
    if preset_family(str(profile.get("preset") or "")) == "cli":
        return "agent_loop_cli"
    return "agent_loop"


class TurnRequestPreparer:
    if TYPE_CHECKING:
        store: SessionStoreProtocol
        coordinator: RuntimeCoordinator | None
        owner_id: str
        _coordination_scope: str
        _lock: asyncio.Lock
        _executions: dict[str, _TurnExecution]

        async def _ensure_accepting_turns(self) -> None: ...

        def _turns_blocked_for_update_locked(self) -> bool: ...

        async def _publish_live_event(
            self,
            execution: _TurnExecution,
            event: StreamEvent,
        ) -> dict[str, Any]: ...

        async def _run_turn(self, execution: _TurnExecution) -> None: ...

        async def _coordinate_execution(self, execution: _TurnExecution) -> None: ...

    async def start_turn(self, payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        await self._ensure_accepting_turns()
        # ``TurnRuntimeManager`` remains a one-version compatibility facade;
        # transport adapters normally strip their envelope before reaching it.
        payload = TurnRequest.model_validate(
            {key: value for key, value in payload.items() if key != "type"}
        ).to_payload()
        persona_explicit = "persona" in payload
        if not payload.get("language"):
            from kagweb.services.settings.interface_settings import (
                get_response_language,
            )

            payload = {**payload, "language": get_response_language(default="en")}
        raw_config = dict(payload.get("config", {}) or {})
        session = await self.store.ensure_session(payload.get("session_id"))
        preferences = session.get("preferences") or {}

        requested_capability = str(payload.get("capability") or "chat")
        capability = requested_capability

        try:
            from kagweb.runtime.request_contracts import validate_capability_config

            validated_public_config = validate_capability_config(capability, raw_config)
        except ValueError as exc:
            raise RuntimeError(str(exc)) from exc
        payload = {
            **payload,
            "capability": capability,
            "requested_capability": requested_capability,
            "config": validated_public_config,
        }
        # Persona is a session-level preference (mirrors llm_selection): an
        # explicit ``persona`` key in the payload — including an empty string,
        # which means "Default" / no persona — wins and is persisted below.
        # Without one, the session's stored preference survives reloads.
        persona_pref = str(
            (payload.get("persona") if "persona" in payload else preferences.get("persona")) or ""
        ).strip()
        payload = {**payload, "persona": persona_pref}
        raw_llm_selection = payload.get("llm_selection")
        if raw_llm_selection is None:
            raw_llm_selection = preferences.get("llm_selection")
        try:
            llm_selection = _llm_selection_dict(raw_llm_selection)
        except ValueError as exc:
            raise RuntimeError(str(exc)) from exc

        # One agent-loop settings read for the whole gate block: the access
        # check needs the block to resolve what the turn requires, and the
        # context window below needs the primary profile from the same block.
        # Read out here rather than inside the non-admin branch so an admin turn
        # gets the context window too — budgeting is every operator's problem.
        from kagweb.services.agent_loop.settings import (
            get_agent_loop_settings,
            resolve_primary_profile,
        )

        agent_loop_block = get_agent_loop_settings()

        # The access gate runs for EVERY non-admin turn, before the two
        # selection branches below — not inside one of them.
        #
        # It used to sit in the `else` arm, which meant a caller who *pinned* an
        # `llm_selection` skipped it completely: that arm only asked whether the
        # chosen model was granted, never whether the turn's actual resource was
        # allowed. Since `llm_selection` is a client-supplied protocol field,
        # any user holding one granted LLM could still start a CLI/ACP agent
        # process — defeating exactly the check that exists to stop that.
        from kagweb.multi_user.context import get_current_user
        from kagweb.multi_user.model_access import (
            has_capability_access,
            redacted_model_access,
        )

        current_user = get_current_user()
        if not current_user.is_admin:
            # Gate on the resource this turn actually needs, not on the LLM
            # grant by default. `chat` with an agent backend configured needs no
            # LLM: the backend brings its own model, and the session
            # title/insight/summary helpers degrade on their own. Gating it on
            # "llm" is what rejected every non-admin turn in a pure agent-loop
            # deployment (backend-llm-deployment.md P0-1).
            required = _effective_required_service(capability, agent_loop_block=agent_loop_block)
            if not has_capability_access(required):
                raise RuntimeError(_access_denied_message(required))

        # A profile may declare the window its backend actually runs with. The
        # executor builds the context (and so the history budget) before the
        # chat capability resolves the profile, so the value has to travel on
        # the payload — an internal key, added after ``TurnRequest`` validation
        # so it is not part of the public schema, and not among the keys
        # ``_request_snapshot_metadata`` persists.
        try:
            primary_profile = resolve_primary_profile(agent_loop_block)
        except Exception:
            primary_profile = None
        context_window = int((primary_profile or {}).get("context_window") or 0)
        # Always written, never merged: the executor trusts this key, so a
        # client-supplied value must not be able to survive here. (Today
        # ``TurnRequest`` forbids extra fields and rejects the whole turn, but
        # that guarantee should not be what stands between a client and the
        # budget planner.) ``0`` means "not configured"; the executor treats it
        # as no override.
        payload["agent_loop_context_window"] = context_window

        if llm_selection:
            try:
                from kagweb.multi_user.model_access import apply_allowed_llm_selection

                llm_selection = apply_allowed_llm_selection(llm_selection) or {}
            except PermissionError as exc:
                raise RuntimeError(str(exc)) from exc
        elif not current_user.is_admin:
            # No pinned selection: pin the first granted-and-available model.
            # With no LLM grant (agent-backend deployment) this stays empty and
            # the turn runs without a scoped model, which is correct — the chat
            # capability never touches KAGWeb's own LLM layer.
            assigned_llms = [
                item
                for item in redacted_model_access(current_user.id).get("llm", [])
                if item.get("available")
            ]
            if assigned_llms:
                llm_selection = {
                    "profile_id": assigned_llms[0].get("profile_id"),
                    "model_id": assigned_llms[0].get("model_id"),
                }
        if llm_selection:
            from kagweb.multi_user.personal_models import merge_personal_llm_profiles
            from kagweb.services.config import get_model_catalog_service
            from kagweb.services.model_selection import (
                LLMSelection,
                apply_llm_selection_to_catalog,
            )

            try:
                # Personal (owner-bound) profiles live in the user's own
                # catalog, so validating against the shared one alone would
                # reject a Codex model the user signed in for themselves —
                # the same merge the resolution path performs (#781).
                apply_llm_selection_to_catalog(
                    merge_personal_llm_profiles(get_model_catalog_service().load()),
                    LLMSelection.from_payload(llm_selection),
                )
            except ValueError as exc:
                raise RuntimeError(str(exc)) from exc
        # If the caller didn't pin a per-turn tool list (e.g. non-web
        # channels or the new web UI which sources tools from
        # /settings/tools), back-fill from the user's saved toggleable-tool
        # preference so the chat pipeline sees the same set the user picked
        # in Settings. Callers that explicitly pass ``tools`` (including
        # an empty list) keep their value untouched.
        if payload.get("tools") is None:
            try:
                from kagweb.services.settings.interface_settings import (
                    get_enabled_optional_tools,
                )

                payload = {**payload, "tools": list(get_enabled_optional_tools())}
            except Exception:
                payload = {**payload, "tools": []}
        # Admin-imposed per-user tool whitelist (grant v2). Sits after the
        # back-fill so explicit caller lists and settings defaults pass the
        # same gate; this is the single enforcement point for every
        # capability's turn.
        from kagweb.multi_user.tool_access import allowed_optional_tools

        allowed_tools = allowed_optional_tools()
        if allowed_tools is not None:
            payload = {
                **payload,
                "tools": [t for t in (payload.get("tools") or []) if t in allowed_tools],
            }
        payload = {**payload, "llm_selection": llm_selection}
        lease = None
        if self.coordinator is not None:
            turn_id = f"turn_{int(time.time() * 1000)}_{uuid.uuid4().hex[:10]}"
            lease = await self.coordinator.acquire_turn(
                turn_id,
                f"{self._coordination_scope}:{session['id']}",
                self.owner_id,
            )
            if lease is None:
                raise RuntimeError("Session already has an active or recovering turn")
        preference_update: dict[str, Any] = {
            "capability": requested_capability,
            "tools": list(payload.get("tools") or []),
            "language": str(payload.get("language") or "en"),
        }

        raw_selection_context = payload.get("selection_tutor_context")
        if isinstance(raw_selection_context, dict):
            selection_tutor_context = _extract_selection_tutor_context(
                {"selection_tutor_context": dict(raw_selection_context)}
            )
            if selection_tutor_context is None:
                raise RuntimeError("Selection tutor context requires selected text")
            try:
                selection_tutor_context = await _resolve_selection_tutor_context(
                    self.store,
                    selection_tutor_context,
                )
            except ValueError as exc:
                raise RuntimeError(str(exc)) from exc
            payload["selection_tutor_context"] = selection_tutor_context
            parent_session_id = str(selection_tutor_context.get("parent_session_id") or "").strip()
            if parent_session_id == session["id"]:
                raise RuntimeError("A selection tutor session cannot parent itself")
            if parent_session_id:
                from kagweb.services.session.organization import (
                    validate_parent_assignment,
                )

                try:
                    parent_session = await validate_parent_assignment(
                        self.store,
                        session_id=session["id"],
                        parent_session_id=parent_session_id,
                    )
                except (LookupError, ValueError) as exc:
                    raise RuntimeError(str(exc)) from exc
                parent_preferences = parent_session.get("preferences") or {}
                preference_update.update(
                    {
                        "parent_session_id": parent_session_id,
                        "session_kind": "selection_tutor",
                        "course_id": str(parent_preferences.get("course_id") or ""),
                    }
                )
        if llm_selection:
            preference_update["llm_selection"] = llm_selection
        if persona_explicit:
            # Persist explicit set AND explicit clear ("" = back to Default).
            preference_update["persona"] = persona_pref
        await self.store.update_session_preferences(session["id"], preference_update)
        try:
            if lease is None:
                turn = await self.store.create_turn(session["id"], capability=capability)
            else:
                turn = await self.store.begin_turn(
                    session["id"],
                    capability=capability,
                    turn_id=lease.turn_id,
                    owner_id=lease.owner_id,
                    fencing_token=lease.fencing_token,
                )
        except Exception:
            if lease is not None and self.coordinator is not None:
                with contextlib.suppress(Exception):
                    await self.coordinator.release_turn(lease)
            raise
        execution = _TurnExecution(
            turn_id=turn["id"],
            session_id=session["id"],
            capability=capability,
            payload=dict(payload),
            lease=lease,
        )
        # Publish an ownership marker before trying to recover another path
        # lease. Two start_turn calls can otherwise interleave after the first
        # turn row is created but before its task is registered, causing the
        # second caller to misclassify that healthy turn as a restart orphan.
        async with self._lock:
            update_blocked = self._turns_blocked_for_update_locked()
            if not update_blocked:
                self._executions[turn["id"]] = execution
        if update_blocked:
            with contextlib.suppress(Exception):
                await self.store.transition_turn(
                    turn["id"],
                    "failed",
                    error="The server is preparing an update; try again after it reconnects",
                    failure_code="rejected",
                )
            raise RuntimeError("The server is preparing an update; try again after it reconnects")
        session_metadata: dict[str, Any] = {
            "session_id": session["id"],
            "turn_id": turn["id"],
        }
        regenerated_from = payload.get("regenerated_from_message_id")
        if regenerated_from is not None:
            session_metadata["regenerated_from_message_id"] = regenerated_from
        superseded_turn_id = payload.get("superseded_turn_id")
        if superseded_turn_id:
            session_metadata["superseded_turn_id"] = str(superseded_turn_id)
        if payload.get("regenerate"):
            session_metadata["regenerate"] = True
        try:
            await self._publish_live_event(
                execution,
                StreamEvent(
                    type=StreamEventType.SESSION,
                    source="turn_runtime",
                    metadata=session_metadata,
                ),
            )
            async with self._lock:
                execution.task = asyncio.create_task(self._run_turn(execution))
                if execution.lease is not None and self.coordinator is not None:
                    execution.coordination_task = asyncio.create_task(
                        self._coordinate_execution(execution)
                    )
        except Exception as exc:
            async with self._lock:
                self._executions.pop(turn["id"], None)
            with contextlib.suppress(Exception):
                await self.store.update_turn_status(turn["id"], "failed", str(exc))
            if lease is not None and self.coordinator is not None:
                with contextlib.suppress(Exception):
                    await self.coordinator.release_turn(lease)
            raise
        return session, turn

    async def regenerate_last_turn(
        self,
        session_id: str,
        overrides: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Re-run the prior user message in ``session_id``.

        Deletes the trailing assistant message (if any), then dispatches a new
        turn with ``persist_user_message=False`` and ``regenerate=True`` so
        the runtime knows not to duplicate the user row or refresh long-term
        memory a second time. The original user message stays in place.
        """
        session_id = str(session_id or "").strip()
        if not session_id:
            raise RuntimeError("nothing_to_regenerate")

        session = await self.store.get_session(session_id)
        if session is None:
            raise RuntimeError("nothing_to_regenerate")

        active = await self.store.get_active_turn(session_id)
        if active is not None:
            raise RuntimeError("regenerate_busy")

        last_user = await self.store.get_last_message(session_id, role="user")
        if last_user is None:
            raise RuntimeError("nothing_to_regenerate")

        last_message = await self.store.get_last_message(session_id)
        previous_turn_id: str | None = None
        if last_message is not None and last_message.get("role") == "assistant":
            for event in last_message.get("events") or []:
                turn_id = str((event or {}).get("turn_id") or "")
                if turn_id:
                    previous_turn_id = turn_id
                    break
            await self.store.delete_message(last_message["id"])

        preferences = session.get("preferences") or {}
        overrides = overrides or {}
        snapshot = {}
        metadata = last_user.get("metadata") or {}
        if isinstance(metadata, dict):
            candidate = metadata.get("request_snapshot") or metadata.get("requestSnapshot")
            if isinstance(candidate, dict):
                snapshot = candidate

        capability = str(
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
        }
        if previous_turn_id:
            payload["superseded_turn_id"] = previous_turn_id
        if llm_selection:
            payload["llm_selection"] = llm_selection
        return await self.start_turn(payload)
