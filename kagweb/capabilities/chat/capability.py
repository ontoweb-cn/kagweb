"""Default ``chat`` capability — agent-loop conversation or framework shell.

Two behaviors, selected by the runtime ``agent_loop`` settings block:

* **No backend configured** (default): the framework-shell stub. Every
  turn completes with a localized notice so the whole stack (API,
  WebSocket, CLI, web UI) stays exercisable — see ARCHITECTURE.md.
* **Backend configured**: the turn is delegated to the external agent
  loop (CLI subprocess or HTTP service, see
  ``kagweb/services/agent_loop/``). Progress streams through the normal
  turn events; the concatenated content becomes the persisted answer.

Integration point for new backends: presets in
``kagweb/services/agent_loop/builtin.py`` and adapters in
``cli_backend.py`` / ``http_backend.py`` — this capability stays
transport-agnostic.
"""

from __future__ import annotations

from typing import Any

from kagweb.capabilities._shared import emit_capability_result
from kagweb.core.capability_protocol import CapabilityManifest, TurnCapability
from kagweb.core.context import UnifiedContext
from kagweb.services.agent_loop import build_agent_loop_backend
from kagweb.services.agent_loop.consult import (
    consult_manifest,
    consult_session_id,
    followup_request,
    parse_consult_directive,
    strip_consult_directive,
)
from kagweb.services.agent_loop.protocol import AgentLoopBackend, AgentLoopEvent
from kagweb.services.agent_loop.settings import (
    consult_budget as read_consult_budget,
)
from kagweb.services.agent_loop.settings import (
    consult_profiles,
    find_consult_profile,
    get_agent_loop_settings,
    resolve_primary_profile,
)
from kagweb.services.i18n import t
from kagweb.services.llm.usage_tracker import UsageTracker
from kagweb.services.settings.interface_settings import get_response_language


class ChatCapability(TurnCapability):
    """Conversation capability: agent-loop backend, or the shell stub."""

    manifest = CapabilityManifest(
        name="chat",
        description=(
            "Default conversation capability: delegates turns to the configured "
            "agent-loop backend (framework-shell notice while none is configured)."
        ),
        stages=["responding"],
        tools_used=[],
        cli_aliases=["chat"],
    )

    async def run(self, context: UnifiedContext, stream) -> None:  # noqa: ANN001
        # One settings read per turn: load_system() hits the JSON file (and
        # may rewrite it), so neither the backend factory nor the request
        # builder may go to disk a second time.
        settings = get_agent_loop_settings()
        primary = resolve_primary_profile(settings)
        backend = build_agent_loop_backend(primary) if primary else None
        if backend is None:
            await self._run_shell_notice(context, stream)
            return
        await self._run_agent_loop(context, stream, backend, primary, settings)

    # ------------------------------------------------------------------
    # Framework-shell stub
    # ------------------------------------------------------------------

    async def _run_shell_notice(self, context: UnifiedContext, stream) -> None:  # noqa: ANN001
        language = get_response_language()
        notice = t(
            "chat.stub_notice",
            language=language,
        )
        await stream.content(notice, source=self.name)
        context.capability_output.agent_output = notice
        context.capability_output.answer_published = True
        await emit_capability_result(
            stream,
            {"response": notice},
            source=self.name,
            usage=UsageTracker(),
        )

    # ------------------------------------------------------------------
    # Agent-loop delegation
    # ------------------------------------------------------------------

    async def _run_agent_loop(
        self,
        context: UnifiedContext,
        stream,  # noqa: ANN001
        backend: AgentLoopBackend,
        primary: dict[str, Any],
        settings: dict[str, Any],
    ) -> None:
        budget = max(0, read_consult_budget(settings))
        consults = consult_profiles(settings) if budget > 0 else []
        language = context.language or "en"
        request = _build_request(
            context,
            session_workspace=bool(primary.get("session_workspace")),
            consult_manifest=(
                consult_manifest(consults, budget=budget, language=language) if consults else None
            ),
        )

        usage: dict[str, Any] = {}
        answer = ""
        asked: set[tuple[str, str]] = set()
        consults_done = 0
        # Pass cap = budget + 1: every consult costs one extra pass, and one
        # runaway loop that keeps emitting directives is cut off here.
        while True:
            answer, pass_usage = await self._run_single_pass(stream, backend, request)
            usage.update({k: v for k, v in pass_usage.items() if v is not None})
            directive = (
                parse_consult_directive(answer) if consults and consults_done < budget else None
            )
            if directive is None:
                break
            key = (directive["agent"], directive["question"])
            if key in asked:
                break
            asked.add(key)
            consults_done += 1
            profile = find_consult_profile(settings, directive["agent"])
            if profile is None:
                # Unknown agent reference: surface it and stop consulting —
                # the spoken answer (minus the directive block) still lands.
                await stream.progress(
                    t(
                        "agent_loop.consult_unknown_agent",
                        agent=directive["agent"],
                        language=language,
                    ),
                    source=self.name,
                    stage="responding",
                )
                answer = strip_consult_directive(answer)
                break
            await stream.tool_call(
                "consult_agent",
                {"agent": str(profile.get("name")), "question": directive["question"]},
                source=self.name,
                stage="responding",
                metadata={"agent_loop_consult": str(profile.get("id"))},
            )
            consult_answer = await self._run_consult(
                stream, settings, profile, directive["question"], request, language
            )
            await stream.tool_result(
                "consult_agent",
                consult_answer or t("agent_loop.consult_empty", language=language),
                source=self.name,
                stage="responding",
                metadata={"agent_loop_consult": str(profile.get("id"))},
            )
            request = followup_request(
                request,
                pass_answer=answer,
                consult_name=str(profile.get("name")),
                consult_answer=consult_answer or t("agent_loop.consult_empty", language=language),
                language=language,
            )

        answer = answer.strip()
        if not answer:
            # A completed turn with no answer text is a backend problem the
            # operator must be able to see; surface it in the trace without
            # failing the turn (partial tool output may still be useful).
            await stream.error(
                t("agent_loop.empty_answer", backend=backend.name),
                source=self.name,
                stage="responding",
                metadata={"non_terminal": True},
            )
        context.capability_output.agent_output = answer
        context.capability_output.answer_published = True
        # RESULT metadata is the shallow-merged payload itself, so keep the
        # backend identity flat instead of nesting it under a "metadata" key.
        await emit_capability_result(
            stream,
            {
                "response": answer,
                "agent_loop": {
                    "backend": backend.name,
                    "usage": usage or None,
                    "consults": consults_done or None,
                },
            },
            source=self.name,
        )

    async def _run_single_pass(
        self,
        stream,  # noqa: ANN001
        backend: AgentLoopBackend,
        request: Any,
    ) -> tuple[str, dict[str, Any]]:
        """Stream one backend pass; return (answer text, usage counters)."""
        answer_parts: list[str] = []
        usage: dict[str, Any] = {}
        async for event in backend.run(request):
            if event.kind == "content" and event.text:
                # A content event is one complete block, not a delta: an agent
                # loop emits a separate block for the text before and after a
                # tool call, and those are distinct paragraphs. Join with the
                # break folded into the later block so the live stream and the
                # persisted answer read identically.
                text = f"\n\n{event.text}" if answer_parts else event.text
                answer_parts.append(event.text)
                await stream.content(text, source="chat", stage="responding")
                continue
            await _emit_agent_loop_event(stream, event, source="chat", stage="responding")
            if event.kind == "usage":
                usage.update({k: v for k, v in event.data.items() if v is not None})
        return "\n\n".join(answer_parts).strip(), usage

    async def _run_consult(
        self,
        stream,  # noqa: ANN001
        settings: dict[str, Any],
        profile: dict[str, Any],
        question: str,
        request: Any,
        language: str,
    ) -> str:
        """Run one consult against a secondary profile; its events surface as
        progress (never as main content), and failures degrade to an error
        note instead of failing the whole turn."""
        from kagweb.services.agent_loop.protocol import AgentLoopError, AgentLoopRequest

        name = str(profile.get("name") or profile.get("preset") or "agent")
        try:
            consult_backend = build_agent_loop_backend(profile)
        except AgentLoopError as exc:
            await stream.error(str(exc), source=self.name, stage="responding")
            return str(exc)
        if consult_backend is None:  # pragma: no cover - consult_profiles filters
            return ""
        consult_request = AgentLoopRequest(
            prompt=question,
            session_id=consult_session_id(request, str(profile.get("id"))),
            language=language,
        )
        answer_parts: list[str] = []
        try:
            async for event in consult_backend.run(consult_request):
                text = event.text or event.name
                if text:
                    await stream.progress(
                        text,
                        source=f"consult:{name}",
                        stage="responding",
                        metadata={
                            "agent_loop_consult": str(profile.get("id")),
                            "kind": event.kind,
                        },
                    )
                if event.kind == "content" and event.text:
                    answer_parts.append(event.text)
        except AgentLoopError as exc:
            await stream.error(str(exc), source=f"consult:{name}", stage="responding")
            return str(exc)
        return "\n\n".join(answer_parts).strip()


def _build_request(
    context: UnifiedContext,
    *,
    session_workspace: bool,
    consult_manifest: str | None = None,
) -> Any:
    from kagweb.services.agent_loop.protocol import AgentLoopRequest

    # Agent loops carry their own system prompt; KAGWeb contributes its
    # per-turn grounding blocks, then the user message.
    blocks = [
        block.strip()
        for block in (
            context.persona_context,
            context.sidebar_context,
            f"Attached sources:\n{context.source_manifest}" if context.source_manifest else "",
            consult_manifest or "",
        )
        if block.strip()
    ]
    prompt = "\n\n".join([*blocks, context.user_message])

    workdir = ""
    if session_workspace and context.session_id:
        try:
            from kagweb.services.path_service import get_path_service

            workdir_path = get_path_service().get_task_workspace("chat", context.session_id)
            # get_task_workspace is a pure path join — create it here or the
            # subprocess spawn fails with a missing cwd on the first turn.
            workdir_path.mkdir(parents=True, exist_ok=True)
            workdir = str(workdir_path)
        except Exception:
            # A missing workspace must degrade to the server cwd, never
            # fail the turn.
            workdir = ""

    history = [
        item
        for item in context.conversation_history
        if isinstance(item, dict)
        and str(item.get("role") or "") in {"user", "assistant", "system"}
        and item.get("content")
    ]
    return AgentLoopRequest(
        prompt=prompt,
        history=history,
        session_id=context.session_id,
        language=context.language or "en",
        workdir=workdir,
    )


async def _emit_agent_loop_event(stream, event: AgentLoopEvent, *, source: str, stage: str) -> None:  # noqa: ANN001
    if event.kind == "content":
        await stream.content(event.text, source=source, stage=stage)
    elif event.kind == "thinking":
        await stream.thinking(event.text, source=source, stage=stage)
    elif event.kind == "tool_call":
        args = event.data.get("args") if isinstance(event.data.get("args"), dict) else None
        metadata: dict[str, Any] = {}
        if event.text:
            metadata["text"] = event.text
        call_id = str(event.data.get("id") or "")
        if call_id:
            # The same id the matching tool_result carries, so the trace can
            # pair a call with its result instead of listing both loose.
            metadata.update({"call_id": call_id, "call_state": "running"})
        await stream.tool_call(
            event.name or "tool",
            args if args is not None else {"input": event.text},
            source=source,
            stage=stage,
            metadata=metadata or None,
        )
    elif event.kind == "tool_result":
        metadata = {}
        call_id = str(event.data.get("id") or "")
        if call_id:
            metadata["call_id"] = call_id
        is_error = event.data.get("is_error")
        if is_error is not None:
            metadata["is_error"] = bool(is_error)
            metadata["call_state"] = "error" if is_error else "complete"
        await stream.tool_result(
            event.name or "tool",
            event.text,
            source=source,
            stage=stage,
            metadata=metadata or None,
        )
    elif event.kind == "progress":
        await stream.progress(event.text, source=source, stage=stage)
    elif event.kind == "error":
        # Mid-stream problems are surfaced in the trace; the turn still
        # completes with whatever answer the loop produced (matches how
        # native LLM error payloads stream as content).
        await stream.error(event.text, source=source, stage=stage)


__all__ = ["ChatCapability"]
