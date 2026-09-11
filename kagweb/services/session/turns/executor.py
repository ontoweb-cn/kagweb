"""Generated behavior slice of the unified turn runtime."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import contextlib
from contextvars import Token
import logging
from typing import TYPE_CHECKING, Any

from kagweb.core.stream import StreamEvent, StreamEventType
from kagweb.services.session.artifact_attachments import (
    artifact_attachments,
    fill_preview_text,
)
from kagweb.services.session.provider_response_state import (
    normalize_provider_response_state,
)

from .._turn_runtime_shared import (
    _LLM_ROUND_CALL_KINDS,
    _assemble_persisted_answer,
    _count_branch_user_turns,
    _extract_selection_tutor_context,
    _format_selection_tutor_context,
    _request_snapshot_metadata,
    _resolve_selection_tutor_context,
    _resolve_turn_outcome,
    _should_capture_assistant_content,
    _TurnExecution,
)

if TYPE_CHECKING:
    from kagweb.runtime.coordination import RuntimeCoordinator
    from kagweb.services.llm.config import LLMConfig
    from kagweb.services.session.protocol import SessionStoreProtocol

logger = logging.getLogger(__name__)


def _payload_context_window(payload: dict[str, Any]) -> int | None:
    """The agent backend's declared context window, or ``None`` when unset.

    The request preparer puts this on the payload because the context is built
    here, before the chat capability resolves the agent-loop profile — so this
    is the only point at which the window is knowable. Absent or non-positive
    values mean "not configured" and leave the budget chain to its own fallback.
    """
    try:
        value = int(payload.get("agent_loop_context_window") or 0)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _count_llm_rounds(events: list[dict[str, Any]]) -> int:
    """Distinct exploration steps in this turn's assistant events.

    A turn needs ≥2 for the insight judge — the badge marks how a multi-step
    exploration *moved*. Rounds are counted by ``call_id`` under the shared
    ``_LLM_ROUND_CALL_KINDS``; a tool call counts as a step too, because the
    bridge mints a round id only when a round carries thinking or prose, so a
    tool-only pass (an assistant message that is just a ``tool_use`` block)
    would otherwise be invisible and a genuinely multi-step turn would score 1.
    """
    step_ids: set[str] = set()
    for event in events:
        meta = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
        call_id = meta.get("call_id")
        if not call_id:
            continue
        if meta.get("call_kind") in _LLM_ROUND_CALL_KINDS or event.get("type") == "tool_call":
            step_ids.add(str(call_id))
    return len(step_ids)


def _ordered_tool_names(events: list[dict[str, Any]]) -> list[str]:
    """Ordered, de-duplicated tool names from the turn's tool_call events."""
    names: list[str] = []
    seen: set[str] = set()
    for event in events:
        if event.get("type") != "tool_call":
            continue
        meta = event.get("metadata") if isinstance(event.get("metadata"), dict) else {}
        name = str(meta.get("tool_name") or meta.get("tool") or event.get("content") or "").strip()
        if name and name not in seen:
            seen.add(name)
            names.append(name)
    return names


class TurnExecutor:
    if TYPE_CHECKING:
        store: SessionStoreProtocol
        coordinator: RuntimeCoordinator | None
        turn_engine: Any
        _lock: asyncio.Lock
        _executions: dict[str, _TurnExecution]
        _reply_queues: dict[str, asyncio.Queue[dict[str, Any] | None]]

        def _create_context_builder(self) -> Any: ...

        async def _publish_live_event(
            self,
            execution: _TurnExecution,
            event: StreamEvent,
        ) -> dict[str, Any]: ...

        async def _flush_buffered_events(self, execution: _TurnExecution) -> None: ...

        async def _transition_execution(
            self,
            execution: _TurnExecution,
            status: str,
            error: str = "",
            *,
            failure_code: str = "",
            retryable: bool = False,
        ) -> bool: ...

        async def _maybe_generate_session_title(
            self,
            *,
            execution: _TurnExecution,
            session_id: str,
            ui_language: str,
        ) -> None: ...

        # Also supplied by the ``SessionTitleService`` mixin at runtime (both
        # classes compose into ``TurnRuntimeManager``); declared here so the
        # static checker sees the method on the class that calls it.
        async def _maybe_generate_turn_insight(
            self,
            *,
            execution: _TurnExecution,
            session_id: str,
            turn_id: str,
            assistant_message_id: int | str | None,
            ui_language: str,
            question: str,
            answer: str,
            tool_names: list[str],
            round_count: int,
        ) -> None: ...

    async def _run_turn(self, execution: _TurnExecution) -> None:
        payload = execution.payload
        session_id = execution.session_id
        capability_name = execution.capability
        turn_id = execution.turn_id
        # Resolved once, up front: the persisted answer's CJK emphasis repair
        # and the post-turn metadata jobs both key off the response language,
        # and the former runs before the latter's block is reached.
        ui_language = str(payload.get("language", "en") or "en")
        attachments = []
        attachment_records = []
        assistant_events: list[dict[str, Any]] = []
        assistant_content = ""
        provider_response_state: dict[str, Any] | None = None
        # Content segments captured live from the stream; concatenated into
        # the persisted answer once the turn ends.
        content_segments: list[tuple[str | None, str]] = []

        def _persisted_answer() -> str:
            # clean_thinking_tags is a second line of defence: providers that
            # inline <think> in the content channel must never be persisted
            # as the user-facing answer.
            return _assemble_persisted_answer(content_segments, language=ui_language)

        # Files the model generated this turn (exec/code_execution artifacts),
        # persisted as assistant-message attachments so the UI shows openable
        # cards. Deduped by URL across the turn's SOURCES events.
        generated_attachments: list[dict[str, Any]] = []
        seen_artifact_urls: set[str] = set()
        stream_done_sent = False
        llm_scope_token: Token[LLMConfig | None] | None = None
        reset_active_llm_selection: Callable[[Token[LLMConfig | None] | None], None] | None = None
        # One queue per turn for ``ask_user`` style pause-resume.
        # Created here (BEFORE the orchestrator runs) so the pipeline can
        # await on the awaitable we publish into ``context.metadata``.
        # Cleaned up unconditionally in the outer ``finally``.
        reply_queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        self._reply_queues[turn_id] = reply_queue

        async def _wait_for_user_reply() -> dict[str, Any] | None:
            # Publish the pause so concurrent turn management can tell
            # "busy generating" apart from "parked, user walked away".
            entered_waiting = await self.store.transition_turn(
                execution.turn_id,
                "waiting_input",
                expected_status="running",
                fencing_token=(
                    execution.lease.fencing_token if execution.lease is not None else None
                ),
            )
            if not entered_waiting:
                execution.lease_lost = self.coordinator is not None
                raise asyncio.CancelledError
            execution.awaiting_user_reply = True
            try:
                return await reply_queue.get()
            finally:
                execution.awaiting_user_reply = False
                # Restore the active execution state before the loop resumes or
                # the outer cancellation handler performs its terminal CAS.
                # A stale fencing token is rejected by the repository.
                with contextlib.suppress(Exception):
                    await asyncio.shield(
                        self.store.transition_turn(
                            execution.turn_id,
                            "running",
                            expected_status="waiting_input",
                            fencing_token=(
                                execution.lease.fencing_token
                                if execution.lease is not None
                                else None
                            ),
                        )
                    )

        try:
            from kagweb.core.context import Attachment, TurnRuntimeContext, UnifiedContext
            from kagweb.services.llm.exceptions import NoModelConfiguredError
            from kagweb.services.model_selection.runtime import (
                activate_llm_selection,
            )
            from kagweb.services.model_selection.runtime import (
                reset_llm_selection as reset_active_llm_selection,
            )

            request_config = dict(payload.get("config", {}) or {})
            selection_tutor_context = _extract_selection_tutor_context(
                {"selection_tutor_context": payload.get("selection_tutor_context")}
            )
            if selection_tutor_context:
                selection_tutor_context = await _resolve_selection_tutor_context(
                    self.store,
                    selection_tutor_context,
                )
            persist_user_message = bool(payload.get("persist_user_message", True))
            is_regenerate = bool(payload.get("regenerate", False))
            raw_user_content = str(payload.get("content", "") or "")
            # Edit-branching tip: when the FE includes ``parent_message_id``
            # (even as ``null``), the new user message attaches at that
            # exact parent — creating a sibling of any existing children
            # and forcing LLM context to come from that parent's ancestor
            # chain only. When the key is absent (legacy callers), the
            # store auto-appends to the latest message in the session.
            branch_parent_explicit = "parent_message_id" in payload
            branch_parent_raw = payload.get("parent_message_id")
            branch_parent_id: int | None
            if branch_parent_explicit:
                try:
                    branch_parent_id = (
                        int(branch_parent_raw) if branch_parent_raw is not None else None
                    )
                except (TypeError, ValueError):
                    branch_parent_id = None
                    branch_parent_explicit = False
            else:
                branch_parent_id = None
            history_references = payload.get("history_references", []) or []

            import base64 as _b64
            import uuid as _uuid

            from kagweb.services.storage import get_attachment_store

            for item in payload.get("attachments", []):
                record = {
                    "type": item.get("type", "file"),
                    "url": item.get("url", ""),
                    "base64": item.get("base64", ""),
                    "filename": item.get("filename", ""),
                    "mime_type": item.get("mime_type", ""),
                    "id": item.get("id", "") or _uuid.uuid4().hex[:12],
                }
                attachment_records.append(record)

            # Persist original bytes to the attachment store before extraction
            # so the frontend preview drawer can fetch the file later. The
            # extractor will clear base64 on documents to keep DB rows lean,
            # but the URL we record here outlives that pruning. Upload errors
            # are non-fatal — extraction still runs from the in-memory base64.
            attachment_store = get_attachment_store()
            for record in attachment_records:
                if record.get("url"):
                    continue  # already hosted (e.g. legacy URL)
                b64 = record.get("base64") or ""
                if not b64:
                    continue
                try:
                    raw_bytes = _b64.b64decode(b64, validate=False)
                except Exception as exc:
                    logger.warning(
                        "skipping attachment upload for %r: invalid base64 (%s)",
                        record.get("filename"),
                        exc,
                    )
                    continue
                try:
                    record["url"] = await attachment_store.put(
                        session_id=session_id,
                        attachment_id=record["id"],
                        filename=record.get("filename", "") or "file",
                        data=raw_bytes,
                        mime_type=record.get("mime_type", "") or "",
                    )
                except Exception as exc:
                    logger.warning(
                        "attachment store rejected %r: %s",
                        record.get("filename"),
                        exc,
                    )

            from kagweb.utils.document_extractor import extract_documents_from_records

            document_texts, attachment_records = extract_documents_from_records(attachment_records)
            attachments = [
                Attachment(
                    type=r.get("type", "file"),
                    url=r.get("url", ""),
                    base64=r.get("base64", ""),
                    filename=r.get("filename", ""),
                    mime_type=r.get("mime_type", ""),
                    id=r.get("id", ""),
                    extracted_text=r.get("extracted_text", ""),
                )
                for r in attachment_records
            ]
            # DB persistence copy: drop base64 unconditionally now that the
            # original bytes live in the attachment store. Image attachments
            # used to keep base64 here (which bloated message rows); the URL
            # is now the stable source for previews.
            persisted_attachment_records = [
                {
                    **{k: v for k, v in r.items() if k != "base64"},
                    "base64": "",
                }
                for r in attachment_records
            ]

            sidebar_system_context = ""
            if selection_tutor_context:
                sidebar_system_context = _format_selection_tutor_context(
                    selection_tutor_context,
                    language=str(payload.get("language", "en") or "en"),
                )

            # A framework-shell deployment may have no model configured at
            # all; the capability decides whether that is fatal. Proceed with
            # a None config so stub capabilities can complete bare turns.
            # Only the "no model at all" subtype takes this path: a model
            # that exists but is misconfigured (missing endpoint / API key)
            # must fail the turn with its real error instead of silently
            # completing as a shell notice. Note this except only covers the
            # no-explicit-selection path — an explicit ``llm_selection``
            # resolves through ``resolve_llm_runtime_config``, which does not
            # raise LLMConfigError.
            try:
                llm_config, llm_scope_token = activate_llm_selection(payload.get("llm_selection"))
            except NoModelConfiguredError:
                llm_config, llm_scope_token = None, None
            builder = self._create_context_builder()

            async def _emit_context_event(event: StreamEvent) -> None:
                if event.source in {"context", "context_builder"}:
                    return
                await self._publish_live_event(execution, event)

            history_result = await builder.build(
                session_id=session_id,
                llm_config=llm_config,
                # The agent backend's own window, put on the payload by the
                # request preparer. Absent (the usual case) leaves the budget
                # chain exactly as it was.
                context_window_override=_payload_context_window(payload),
                language=payload.get("language", "en"),
                on_event=_emit_context_event,
                leaf_message_id=branch_parent_id,
            )

            source_manifest_text = ""
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
                language=str(payload.get("language", "en") or "en"),
            )
            source_manifest_text, source_index = render_manifest(inventory)
            effective_user_message = raw_user_content
            conversation_history = list(history_result.conversation_history)
            conversation_context_text = history_result.context_text

            # SQLite returns integer rowids; PocketBase returns its string
            # record ids. Both are opaque to this layer — they only flow into
            # ``parent_message_id`` chaining and the DONE reconcile metadata.
            new_user_message_id: int | str | None = None
            if persist_user_message:
                # Pass parent explicitly only when the FE pinned it (covers
                # both branched edits with a positive id and root edits
                # with explicit null). Otherwise let the store auto-append.
                parent_kwargs: dict[str, Any] = (
                    {"parent_message_id": branch_parent_id} if branch_parent_explicit else {}
                )
                new_user_message_id = await self.store.add_message(
                    session_id=session_id,
                    role="user",
                    content=raw_user_content,
                    capability=capability_name,
                    attachments=persisted_attachment_records,
                    metadata=_request_snapshot_metadata(
                        payload=payload,
                        content=raw_user_content,
                        capability=capability_name,
                        config=request_config,
                        attachments=persisted_attachment_records,
                        history_references=history_references,
                        llm_selection=payload.get("llm_selection"),
                    ),
                    **parent_kwargs,
                )

            context = UnifiedContext(
                session_id=session_id,
                user_message=effective_user_message,
                conversation_history=conversation_history,
                enabled_tools=payload.get("tools"),
                # Selected-text tutoring must stay isolated from global
                # memory and every other auto-mounted built-in.
                allowed_builtin_tools=[] if selection_tutor_context else None,
                active_capability=payload.get("capability"),
                attachments=attachments,
                config_overrides=request_config,
                language=payload.get("language", "en"),
                sidebar_context=sidebar_system_context,
                source_manifest=source_manifest_text,
                runtime=TurnRuntimeContext(
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
                    "llm_selection": payload.get("llm_selection") or {},
                    "llm_model": str(getattr(llm_config, "model", "") or ""),
                    "llm_provider": str(getattr(llm_config, "provider_name", "") or ""),
                    "llm_reasoning_effort": str(getattr(llm_config, "reasoning_effort", "") or ""),
                    # Per-turn full-text payload for read_source-style
                    # consumers. Empty when no sources are attached.
                    "source_index": source_index,
                },
            )

            pending_done_event: StreamEvent | None = None
            async for event in self.turn_engine.execute(context):
                if event.type == StreamEventType.SESSION:
                    continue
                if event.type == StreamEventType.DONE:
                    pending_done_event = event
                    continue
                payload_event = await self._publish_live_event(execution, event)
                if payload_event.get("type") not in {"done", "session"}:
                    assistant_events.append(payload_event)
                if _should_capture_assistant_content(event):
                    call_id = (event.metadata or {}).get("call_id")
                    content_segments.append((str(call_id) if call_id else None, event.content))
                for attachment in artifact_attachments(event):
                    if attachment["url"] not in seen_artifact_urls:
                        seen_artifact_urls.add(attachment["url"])
                        generated_attachments.append(attachment)

            provider_response_state = normalize_provider_response_state(
                context.runtime.provider_response_state
            )
            assistant_provider_metadata = (
                {"provider_response_state": provider_response_state}
                if provider_response_state is not None
                else None
            )

            # Office binaries the browser cannot render need their text pulled
            # out now, while the files are still on disk, or their preview card
            # opens empty. Skipped on the cancelled path below: that one is
            # already unwinding and must not start new blocking work.
            await fill_preview_text(generated_attachments)

            # The persisted answer is the content captured live from the
            # stream (thinking tags stripped as a final defence).
            assistant_content = _persisted_answer()

            # Assistant continues the same branch as the user message it
            # answers. If we just persisted a new user row we chain off
            # that; if we did not (regenerate path) and the caller pinned a
            # parent, we use it; otherwise we let the store auto-append
            # (legacy behavior).
            if new_user_message_id is not None:
                assistant_message_id = await self.store.add_message(
                    session_id=session_id,
                    role="assistant",
                    content=assistant_content,
                    capability=capability_name,
                    events=assistant_events,
                    attachments=generated_attachments or None,
                    parent_message_id=new_user_message_id,
                    metadata=assistant_provider_metadata,
                )
            elif branch_parent_explicit:
                assistant_message_id = await self.store.add_message(
                    session_id=session_id,
                    role="assistant",
                    content=assistant_content,
                    capability=capability_name,
                    events=assistant_events,
                    attachments=generated_attachments or None,
                    parent_message_id=branch_parent_id,
                    metadata=assistant_provider_metadata,
                )
            else:
                assistant_message_id = await self.store.add_message(
                    session_id=session_id,
                    role="assistant",
                    content=assistant_content,
                    capability=capability_name,
                    events=assistant_events,
                    attachments=generated_attachments or None,
                    metadata=assistant_provider_metadata,
                )
            turn_status, turn_error = _resolve_turn_outcome(
                assistant_events,
                pending_done_event,
            )
            if pending_done_event is None:
                pending_done_event = StreamEvent(
                    type=StreamEventType.DONE,
                    source=capability_name,
                    metadata={"status": turn_status},
                )
            else:
                pending_done_event.metadata = {
                    **pending_done_event.metadata,
                    "status": turn_status,
                }
            # Attach the persisted row ids so the frontend can reconcile its
            # optimistic (negative) message ids with a targeted in-place swap
            # instead of refetching and re-rendering the whole session.
            persisted_ids = {
                key: value
                for key, value in (
                    ("user_message_id", new_user_message_id),
                    ("assistant_message_id", assistant_message_id),
                )
                if value
            }
            if persisted_ids:
                pending_done_event.metadata = {**pending_done_event.metadata, **persisted_ids}
            # Commit all non-terminal events and the terminal row before DONE
            # becomes visible. The DONE envelope itself is then appended and
            # synchronously flushed, so a reconnect can never observe a
            # terminal row with a missing durable event prefix.
            await self._flush_buffered_events(execution)
            transitioned = await self._transition_execution(execution, turn_status, turn_error)
            if not transitioned:
                execution.lease_lost = True
                raise asyncio.CancelledError
            await self._publish_live_event(execution, pending_done_event)
            stream_done_sent = True
            await self._flush_buffered_events(execution)
            if not is_regenerate and turn_status == "completed":
                # Post-turn metadata (the session title and the insight badge)
                # runs after DONE so the composer and duration clock stop as
                # soon as the answer is saved; the frontend keeps this socket
                # open briefly so the later frames can still arrive. The two
                # jobs are independent and run concurrently: awaiting them in
                # series would let a slow title eat the insight's socket window
                # — and hold the session's turn lease for up to 40s, rejecting
                # the user's next message in the meantime.
                title_result, insight_result = await asyncio.gather(
                    self._maybe_generate_session_title(
                        execution=execution,
                        session_id=session_id,
                        ui_language=ui_language,
                    ),
                    self._maybe_generate_turn_insight(
                        execution=execution,
                        session_id=session_id,
                        turn_id=turn_id,
                        assistant_message_id=assistant_message_id,
                        ui_language=ui_language,
                        question=effective_user_message,
                        answer=assistant_content,
                        tool_names=_ordered_tool_names(assistant_events),
                        round_count=_count_llm_rounds(assistant_events),
                    ),
                    return_exceptions=True,
                )
                if isinstance(title_result, BaseException):
                    # Not debug: this step is the only thing that names a
                    # conversation, and it has no other error surface. Hiding
                    # its failures below the default log level is what let a
                    # broken title path go unnoticed.
                    logger.warning(
                        "Session title generation failed for turn %s",
                        turn_id,
                        exc_info=title_result,
                    )
                if isinstance(insight_result, BaseException):
                    logger.debug(
                        "Turn insight generation failed for turn %s",
                        turn_id,
                        exc_info=insight_result,
                    )
            # Flush once every terminal/post-turn event (DONE, and the title
            # ``session_meta`` above) has been published, not before: a
            # client that reconnects after this task's ``finally`` pops
            # ``execution`` from ``_executions`` falls back entirely to this
            # persisted backlog, and ``subscribe_turn`` synthesises an
            # id-less DONE when it finds none there -- permanently orphaning
            # the just-persisted assistant reply from that client's
            # reconcile path (it can still see the message after a full
            # session reload, since the row itself is fine; only the
            # targeted in-place swap is unreachable).
            await self._flush_buffered_events(execution)
        except asyncio.CancelledError:
            if execution.lease_lost:
                # The owner can no longer prove it holds the fencing token.
                # Do not publish or persist anything else; leader recovery
                # preserves the shared stream and writes worker_lost.
                raise
            terminal_status = "failed" if execution.shutdown_requested else "cancelled"
            terminal_error = (
                "Server shutdown interrupted this turn"
                if execution.shutdown_requested
                else "Turn cancelled"
            )
            failure_code = "server_shutdown" if execution.shutdown_requested else ""
            retryable = execution.shutdown_requested
            if not stream_done_sent:
                await self._publish_live_event(
                    execution,
                    StreamEvent(
                        type=StreamEventType.ERROR,
                        source=capability_name,
                        content=terminal_error,
                        metadata={
                            "turn_terminal": True,
                            "status": terminal_status,
                            "error_code": failure_code,
                            "retryable": retryable,
                        },
                    ),
                )
            with contextlib.suppress(Exception):
                await self._flush_buffered_events(execution)
            # Best-effort: persist what the turn already produced (streamed
            # answer text, trace events, generated files) so cancelling a
            # turn does not erase visible work — files the model created are
            # on disk either way and must stay reachable. Shielded because
            # we are already unwinding a cancellation. Every step is
            # suppressed separately so the status update below always runs —
            # a turn left "running" gets mislabelled as a restart orphan.
            partial_content = _persisted_answer()
            if partial_content or generated_attachments or assistant_events:
                with contextlib.suppress(Exception):
                    await asyncio.shield(
                        self.store.add_message(
                            session_id=session_id,
                            role="assistant",
                            content=partial_content,
                            capability=capability_name,
                            events=assistant_events,
                            attachments=generated_attachments or None,
                            metadata=(
                                {"provider_response_state": provider_response_state}
                                if provider_response_state is not None
                                else None
                            ),
                        )
                    )
            transitioned = False
            with contextlib.suppress(Exception):
                transitioned = await self._transition_execution(
                    execution,
                    terminal_status,
                    terminal_error,
                    failure_code=failure_code,
                    retryable=retryable,
                )
            if not stream_done_sent and transitioned:
                await self._publish_live_event(
                    execution,
                    StreamEvent(
                        type=StreamEventType.DONE,
                        source=capability_name,
                        metadata={
                            "status": terminal_status,
                            "error_code": failure_code,
                            "retryable": retryable,
                        },
                    ),
                )
                stream_done_sent = True
                with contextlib.suppress(Exception):
                    await self._flush_buffered_events(execution)
            raise
        except Exception as exc:
            if stream_done_sent:
                logger.error(
                    "Post-stream persistence for turn %s failed: %s",
                    turn_id,
                    exc,
                    exc_info=True,
                )
                # Suppress each step separately: a flush failure must not
                # also skip the status update, or the turn stays "running"
                # forever and gets mislabelled as a server-restart orphan.
                with contextlib.suppress(Exception):
                    await self._flush_buffered_events(execution)
                with contextlib.suppress(Exception):
                    await self._transition_execution(
                        execution,
                        "failed",
                        str(exc),
                        failure_code="internal_error",
                        retryable=True,
                    )
            else:
                logger.error("Turn %s failed: %s", turn_id, exc, exc_info=True)
                await self._publish_live_event(
                    execution,
                    StreamEvent(
                        type=StreamEventType.ERROR,
                        source=capability_name,
                        content=str(exc),
                        metadata={"turn_terminal": True, "status": "failed"},
                    ),
                )
                await self._publish_live_event(
                    execution,
                    StreamEvent(
                        type=StreamEventType.DONE,
                        source=capability_name,
                        metadata={"status": "failed"},
                    ),
                )
                with contextlib.suppress(Exception):
                    await self._flush_buffered_events(execution)
                await self._transition_execution(
                    execution,
                    "failed",
                    str(exc),
                    failure_code="internal_error",
                    retryable=True,
                )
        finally:
            if llm_scope_token is not None and reset_active_llm_selection is not None:
                reset_active_llm_selection(llm_scope_token)
            # Drop the reply queue first — any in-flight ``submit_user_reply``
            # that finds the queue gone will return ``False`` rather than
            # accumulating on a dead turn.
            self._reply_queues.pop(turn_id, None)
            async with self._lock:
                current = self._executions.get(turn_id)
                if current is not None:
                    for subscriber in current.subscribers:
                        with contextlib.suppress(asyncio.QueueFull):
                            subscriber.queue.put_nowait(None)
                    self._executions.pop(turn_id, None)
            coordination_task = execution.coordination_task
            if (
                coordination_task is not None
                and coordination_task is not asyncio.current_task()
                and not coordination_task.done()
            ):
                coordination_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await coordination_task
            if execution.lease is not None and self.coordinator is not None:
                with contextlib.suppress(Exception):
                    await self.coordinator.release_turn(execution.lease)
            # A turn may have parsed large attachments or built substantial
            # temporary prompts/results. Reclaim after this coroutine returns,
            # outside the user-visible streaming path.
            from kagweb.runtime.memory_reclaim import schedule_memory_reclaim

            schedule_memory_reclaim()
