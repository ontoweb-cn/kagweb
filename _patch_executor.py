"""One-shot Phase 1 patch: executor.py surgery stage 2. Run from repo root."""
import io
import py_compile

path = "kagweb/services/session/turns/executor.py"
with io.open(path, "r", encoding="utf-8") as f:
    src = f.read()


def cut(src, start, end):
    i = src.index(start)
    j = src.index(end, i)
    return src[:i] + src[j:]


def rep(src, old, new, count=1):
    assert old in src, f"NOT FOUND: {old[:90]!r}"
    return src.replace(old, new, count)


# ── skills + learner profile block: keep persona + learner profile, drop skills ──
src = rep(
    src,
    """            from kagweb.multi_user.context import get_current_user
            from kagweb.multi_user.paths import get_admin_path_service
            from kagweb.multi_user.skill_access import assigned_skill_ids
            from kagweb.services.persona import PersonaService, get_persona_service
            from kagweb.services.skill.service import SkillService, render_skills_manifest
""",
    """            from kagweb.multi_user.context import get_current_user
            from kagweb.multi_user.paths import get_admin_path_service
            from kagweb.services.persona import PersonaService, get_persona_service
""",
)
src = cut(
    src,
    """            # Skills: never user-selected per turn. The model sees a""",
    """            # Chat capability uses the lightweight manifest + read_source""",
)
src = rep(
    src,
    """            # Chat capability uses the lightweight manifest + read_source
            # affordance (no upstream LLM call, no wholesale-dump into the
            # user message). All other capabilities keep the legacy concat
            # path because their internal pipelines consume the named blocks
            # (``[Notebook Context]`` etc.) directly.
            is_chat_capability = (capability_name or "") in {"", "chat"}

            source_manifest_text = ""
            source_index: dict[str, str] = {}

            if is_chat_capability:
                from kagweb.services.session.source_inventory import (
                    build_inventory,
                    render_manifest,
                )

                resolved_notebook_records = (
                    get_notebook_manager().get_records_by_references(notebook_references)
                    if notebook_references
                    else []
                )
                # Current turn ordinal = (#user messages on this branch's
                # ancestor chain) + 1. ``_count_branch_user_turns`` walks
                # the same lineage the inventory builder uses, so we agree
                # on what "turn N" means for the historical labels.
                current_turn_ordinal = (
                    await _count_branch_user_turns(self.store, session_id, branch_parent_id) + 1
                )
                inventory = await build_inventory(
                    self.store,
                    session_id=session_id,
                    leaf_message_id=branch_parent_id,
                    current_turn_ordinal=current_turn_ordinal,
                    fresh_attachment_records=attachment_records,
                    fresh_notebook_records=resolved_notebook_records,
                    fresh_book_context_text=book_context,
                    fresh_book_references=book_references,
                    fresh_history_session_ids=history_references,
                    fresh_question_entry_ids=question_notebook_references,
                    fresh_partner_group_references=partner_group_references,
                    fresh_reading_references=reading_references,
                    language=str(payload.get("language", "en") or "en"),
                )
                source_manifest_text, source_index = render_manifest(inventory)
                effective_user_message = raw_user_content
            else:""",
    """            source_manifest_text = ""
            source_index: dict[str, str] = {}

            from kagweb.services.session.source_inventory import (
                build_inventory,
                render_manifest,
            )

            # Current turn ordinal = (#user messages on this branch's
            # ancestor chain) + 1. ``_count_branch_user_turns`` walks
            # the same lineage the inventory builder uses, so we agree
            # on what "turn N" means for the historical labels.
            current_turn_ordinal = (
                await _count_branch_user_turns(self.store, session_id, branch_parent_id) + 1
            )
            inventory = await build_inventory(
                self.store,
                session_id=session_id,
                leaf_message_id=branch_parent_id,
                current_turn_ordinal=current_turn_ordinal,
                fresh_attachment_records=attachment_records,
                fresh_history_session_ids=history_references,
                fresh_partner_group_references=partner_group_references,
                language=str(payload.get("language", "en") or "en"),
            )
            source_manifest_text, source_index = render_manifest(inventory)
            effective_user_message = raw_user_content
            if False:""",
)

# ── else-branch body (notebook analysis / history concat / question bank) ──
src = cut(
    src,
    """                if notebook_references:
                    referenced_records = get_notebook_manager().get_records_by_references(""",
    """            # A mastery topic carries materials the learner chose once, in the""",
)
src = cut(
    src,
    """            # A mastery topic carries materials the learner chose once, in the""",
    """            conversation_history = list(history_result.conversation_history)""",
)

# ── user message persistence + snapshot metadata call ──
src = rep(
    src,
    """                    metadata=_request_snapshot_metadata(
                        payload=payload,
                        content=raw_user_content,
                        capability=capability_name,
                        config=request_config,
                        attachments=persisted_attachment_records,
                        notebook_references=notebook_references,
                        history_references=history_references,
                        partner_group_references=partner_group_references,
                        question_notebook_references=question_notebook_references,
                        book_references=book_references,
                        reading_references=reading_references,
                        persona=active_persona,
                        memory_references=memory_references,
                        llm_selection=payload.get("llm_selection"),
                    ),""",
    """                    metadata=_request_snapshot_metadata(
                        payload=payload,
                        content=raw_user_content,
                        capability=capability_name,
                        config=request_config,
                        attachments=persisted_attachment_records,
                        history_references=history_references,
                        partner_group_references=partner_group_references,
                        persona=active_persona,
                        llm_selection=payload.get("llm_selection"),
                    ),""",
)

# ── UnifiedContext construction ──
src = rep(
    src,
    """                active_capability=payload.get("capability"),
                knowledge_bases=payload.get("knowledge_bases", []),
                attachments=attachments,""",
    """                active_capability=payload.get("capability"),
                attachments=attachments,""",
)
src = rep(
    src,
    """                runtime=TurnRuntimeContext(
                    turn_id=turn_id,
                    wait_for_user_reply=_wait_for_user_reply,
                    subagent_consult_budget=payload.get("subagent_consult_budget"),
                ),
                metadata={
                    "conversation_summary": history_result.conversation_summary,
                    "conversation_context_text": conversation_context_text,
                    "history_token_count": history_result.token_count,
                    "history_budget": history_result.budget,
                    "turn_id": turn_id,
                    "question_followup_context": followup_question_context or {},
                    "selection_tutor_context": selection_tutor_context or {},
                    "notebook_references": notebook_references,
                    "history_references": history_references,
                    "partner_group_references": partner_group_references,
                    "question_notebook_references": question_notebook_references,
                    "book_references": book_references,
                    "course_id": str(payload.get("course_id") or ""),
                    "course_conventions": str(payload.get("course_conventions") or ""),
                    "reading_references": reading_references,
                    "learner_profile_prompt": learner_profile_prompt,
                    "mastery_path_id": _mastery_path_id(payload.get("mastery_path_id")),
                    "mastery_mode": workspace_mode == WORKSPACE_MODE_MASTERY,
                    "mastery_path_lease_managed": mastery_lease_managed,
                    # Immersive reading: the open material activates the reading
                    # capability and binds its tools; the viewport tells the
                    # model where the user is actually looking.
                    "reading_material_id": _reading_material_id(payload.get("reading_material_id")),
                    "reading_material_revision": _reading_material_revision(
                        payload.get("reading_material_revision")
                    ),
                    "reading_workspace_id": _reading_workspace_id(
                        payload.get("reading_workspace_id")
                    ),
                    "immersive_reading_mode": workspace_mode == WORKSPACE_MODE_READING,
                    "reading_viewport": _reading_viewport(payload.get("reading_viewport")),
                    "timed_media_id": _timed_media_id(payload.get("timed_media_id")),
                    "timed_media_viewport": _timed_media_viewport(
                        payload.get("timed_media_viewport")
                    ),
                    "book_context": book_context,
                    "book_context_warnings": book_context_result.warnings,
                    "memory_references": memory_references,
                    "question_bank_context": question_bank_context,
                    "memory_context": memory_context,
                    "active_persona": active_persona,
                    "llm_selection": payload.get("llm_selection") or {},
                    "llm_model": str(getattr(llm_config, "model", "") or ""),
                    "llm_provider": str(getattr(llm_config, "provider_name", "") or ""),
                    "llm_reasoning_effort": str(getattr(llm_config, "reasoning_effort", "") or ""),
                    "capability_route": payload.get("capability_route"),
                    # Per-turn full-text payload for read_source. Empty when
                    # the manifest is empty (non-chat capabilities, or chat
                    # turns with no attached sources). Consumed by the chat
                    # pipeline's tool kwargs injector, and — a non-empty value
                    # here specifically — by the explore_context pre-pass.
                    "source_index": source_index,
                    # A mastery topic's materials, read on demand by the
                    # tutor's own read_source calls (see
                    # ``MasteryLoopCapability.augment_kwargs``). Kept separate
                    # from ``source_index`` so topic materials never trigger
                    # explore_context's forced pre-pass.
                    "mastery_topic_source_index": mastery_topic_source_index,
                },
            )""",
    """                runtime=TurnRuntimeContext(
                    turn_id=turn_id,
                    wait_for_user_reply=_wait_for_user_reply,
                ),
                metadata={
                    "conversation_summary": history_result.conversation_summary,
                    "conversation_context_text": conversation_context_text,
                    "history_token_count": history_result.token_count,
                    "history_budget": history_result.budget,
                    "turn_id": turn_id,
                    "selection_tutor_context": selection_tutor_context or {},
                    "history_references": history_references,
                    "partner_group_references": partner_group_references,
                    "learner_profile_prompt": learner_profile_prompt,
                    "memory_context": memory_context,
                    "active_persona": active_persona,
                    "llm_selection": payload.get("llm_selection") or {},
                    "llm_model": str(getattr(llm_config, "model", "") or ""),
                    "llm_provider": str(getattr(llm_config, "provider_name", "") or ""),
                    "llm_reasoning_effort": str(getattr(llm_config, "reasoning_effort", "") or ""),
                    # Per-turn full-text payload for read_source-style
                    # consumers. Empty when no sources are attached.
                    "source_index": source_index,
                },
            )""",
)

# ── event loop: DONE capability_route + ask_user stamp + narration tracking ──
src = rep(
    src,
    """                if event.type == StreamEventType.DONE:
                    pending_done_event = event
                    capability_route = payload.get("capability_route")
                    if isinstance(capability_route, dict):
                        pending_done_event.metadata = {
                            **pending_done_event.metadata,
                            "capability_route": dict(capability_route),
                        }
                    continue
                payload_event = await self._publish_live_event(execution, event)
                if payload_event.get("type") not in {"done", "session"}:
                    # A card reply lives inside this assistant row. Persist
                    # the exact user-facing answer boundary so future context
                    # can replay assistant -> user -> assistant in order.
                    _stamp_ask_user_content_offset(payload_event, _persisted_answer())
                    assistant_events.append(payload_event)
                if _should_capture_assistant_content(event):
                    call_id = (event.metadata or {}).get("call_id")
                    content_segments.append((str(call_id) if call_id else None, event.content))
                narration_call_id = _narration_marker_call_id(event)
                if narration_call_id:
                    narration_call_ids.add(narration_call_id)
                for attachment in artifact_attachments(event):""",
    """                if event.type == StreamEventType.DONE:
                    pending_done_event = event
                    continue
                payload_event = await self._publish_live_event(execution, event)
                if payload_event.get("type") not in {"done", "session"}:
                    assistant_events.append(payload_event)
                if _should_capture_assistant_content(event):
                    call_id = (event.metadata or {}).get("call_id")
                    content_segments.append((str(call_id) if call_id else None, event.content))
                for attachment in artifact_attachments(event):""",
)

# ── mastery path change publish after stream ──
src = cut(
    src,
    """            # A mastery turn may have changed which path it is on""",
    """            # Office binaries the browser cannot render need their text pulled""",
)

# ── finally: mastery lease release ──
src = rep(
    src,
    """            self._reply_queues.pop(turn_id, None)
            if bool(payload.get("mastery_path_lease_managed")):
                from kagweb.learning.storage import LearningStore

                # By turn, not by the path the turn started on: mastery_switch
                # can move a turn onto a different path mid-flight, and freeing
                # the original id would release someone else's lease while
                # leaking the one this turn actually holds.
                with contextlib.suppress(Exception):
                    await asyncio.shield(
                        asyncio.to_thread(LearningStore().release_leases_for_turn, turn_id)
                    )
            async with self._lock:""",
    """            self._reply_queues.pop(turn_id, None)
            async with self._lock:""",
)

with io.open(path, "w", encoding="utf-8", newline="") as f:
    f.write(src)
py_compile.compile(path, doraise=True)
print("stage 2 OK")
