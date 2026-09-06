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
from kagweb.services.agent_loop.protocol import AgentLoopBackend, AgentLoopEvent
from kagweb.services.agent_loop.settings import get_agent_loop_settings
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
        backend = build_agent_loop_backend(settings)
        if backend is None:
            await self._run_shell_notice(context, stream)
            return
        await self._run_agent_loop(context, stream, backend, settings)

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
        settings: dict[str, Any],
    ) -> None:
        request = _build_request(context, session_workspace=bool(settings.get("session_workspace")))

        answer_parts: list[str] = []
        usage: dict[str, Any] = {}
        async for event in backend.run(request):
            await _emit_agent_loop_event(stream, event, source=self.name, stage="responding")
            if event.kind == "content" and event.text:
                answer_parts.append(event.text)
            elif event.kind == "usage":
                usage.update({k: v for k, v in event.data.items() if v is not None})

        answer = "".join(answer_parts).strip()
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
                "agent_loop": {"backend": backend.name, "usage": usage or None},
            },
            source=self.name,
        )


def _build_request(context: UnifiedContext, *, session_workspace: bool) -> Any:
    from kagweb.services.agent_loop.protocol import AgentLoopRequest

    # Agent loops carry their own system prompt; KAGWeb contributes its
    # per-turn grounding blocks, then the user message.
    blocks = [
        block.strip()
        for block in (
            context.persona_context,
            context.sidebar_context,
            f"Attached sources:\n{context.source_manifest}" if context.source_manifest else "",
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
        await stream.tool_call(
            event.name or "tool",
            args if args is not None else {"input": event.text},
            source=source,
            stage=stage,
            metadata={"text": event.text} if event.text else None,
        )
    elif event.kind == "tool_result":
        await stream.tool_result(event.name or "tool", event.text, source=source, stage=stage)
    elif event.kind == "progress":
        await stream.progress(event.text, source=source, stage=stage)
    elif event.kind == "error":
        # Mid-stream problems are surfaced in the trace; the turn still
        # completes with whatever answer the loop produced (matches how
        # native LLM error payloads stream as content).
        await stream.error(event.text, source=source, stage=stage)


__all__ = ["ChatCapability"]
