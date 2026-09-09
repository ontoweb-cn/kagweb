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

from collections import deque
import math
from pathlib import Path
import time
from typing import Any

from kagweb.capabilities._shared import emit_capability_result
from kagweb.core.capability_protocol import CapabilityManifest, TurnCapability
from kagweb.core.context import UnifiedContext
from kagweb.core.trace import build_trace_metadata, merge_trace_metadata, new_call_id
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

# Mechanical cap on the observation excerpt that rides beside each tool result:
# the reader sees what the tool saw without expanding the row, and one verbose
# tool cannot bloat the turn's event history.
_OBSERVATION_EXCERPT_LIMIT = 200


def _as_count(value: Any) -> int | None:
    """A usable token count, or ``None`` — never a coerced or fabricated one."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value) or value < 0:
        return None
    return int(value)


def _normalize_usage(usage: dict[str, Any]) -> dict[str, Any]:
    """Map a backend's usage counters onto the DSL's token contract.

    Only reported numbers survive. ``total`` is written when the backend sent
    one, or when the counters describe a whole pass; a ``cumulative`` scope
    means at least one counter is a running total, so a synthesized sum would
    be a lie — that pair ships without ``total`` and the scope label says why.
    """
    prompt = _as_count(usage.get("input_tokens", usage.get("prompt_tokens")))
    completion = _as_count(usage.get("output_tokens", usage.get("completion_tokens")))
    if prompt is None and completion is None:
        return {}
    scope = str(usage.get("usage_scope") or "pass")
    normalized: dict[str, Any] = {
        "prompt_tokens": prompt or 0,
        "completion_tokens": completion or 0,
        "usage_scope": scope,
    }
    total = _as_count(usage.get("total_tokens"))
    if total is None and scope != "cumulative":
        total = normalized["prompt_tokens"] + normalized["completion_tokens"]
    if total is not None:
        normalized["total_tokens"] = total
    return normalized


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
        # Only the CLI family runs in a working directory; the HTTP family
        # executes in the operator's own service, so resolving or creating one
        # on its behalf would be a pointless filesystem side effect.
        workdir = ""
        # getattr, not an attribute read: a duck-typed backend that predates
        # the field simply gets no working directory, which is the safe default.
        if getattr(backend, "uses_workdir", False):
            workdir = await _prepare_workdir(
                _resolve_profile_workdir(primary, settings),
                stream,
                source=self.name,
                language=language,
            )
        request = _build_request(
            context,
            session_workspace=bool(primary.get("session_workspace")),
            workdir=workdir,
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
            answer, pass_usage, bridge = await self._run_single_pass(stream, backend, request)
            usage.update({k: v for k, v in pass_usage.items() if v is not None})
            directive = (
                parse_consult_directive(answer) if consults and consults_done < budget else None
            )
            key = (directive["agent"], directive["question"]) if directive else None
            if key is not None and key in asked:
                key = None
            profile = find_consult_profile(settings, key[0]) if key else None
            if profile is None:
                # This pass holds the turn's answer — settle it as final.
                if key is not None:
                    # Unknown agent reference: surface it and stop consulting —
                    # the spoken answer (minus the directive block) still lands.
                    asked.add(key)
                    consults_done += 1
                    await stream.progress(
                        t(
                            "agent_loop.consult_unknown_agent",
                            agent=key[0],
                            language=language,
                        ),
                        source=self.name,
                        stage="responding",
                    )
                    answer = strip_consult_directive(answer)
                await bridge.finish(terminal=True, usage=pass_usage)
                break
            # Another pass follows, so this one is only its run-up: settling it
            # as final would fold the activity trace while the loop still works.
            await bridge.finish(terminal=False, usage=pass_usage)
            asked.add(key)
            consults_done += 1
            await stream.tool_call(
                "consult_agent",
                {"agent": str(profile.get("name")), "question": key[1]},
                source=self.name,
                stage="responding",
                metadata={"agent_loop_consult": str(profile.get("id"))},
            )
            consult_answer = await self._run_consult(
                stream, settings, profile, key[1], request, language
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
    ) -> tuple[str, dict[str, Any], _AgentLoopRoundBridge]:
        """Stream one backend pass.

        Returns the answer text, the usage counters, and the bridge that
        carries this pass's trace. The caller settles the bridge because only
        it knows whether another pass follows (a consult runs the loop again) —
        a run-up pass must not close as the turn's final round.
        """
        answer_parts: list[str] = []
        usage: dict[str, Any] = {}
        bridge = _AgentLoopRoundBridge(stream, source="chat", stage="responding")
        async for event in backend.run(request):
            if event.kind == "usage":
                usage.update({k: v for k, v in event.data.items() if v is not None})
                continue
            if event.kind == "content" and event.text:
                # A content event is one complete block, not a delta: an agent
                # loop emits a separate block for the text before and after a
                # tool call, and those are distinct paragraphs. Join with the
                # break folded into the later block so the live stream and the
                # persisted answer read identically.
                text = f"\n\n{event.text}" if answer_parts else event.text
                answer_parts.append(event.text)
                await bridge.content(text)
                continue
            await bridge.forward(event)
        return "\n\n".join(answer_parts).strip(), usage, bridge

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
        # A consult CLI profile runs in its own configured directory too; the
        # settings page offers the field for every CLI profile, so ignoring it
        # here would make the control a lie.
        consult_workdir = ""
        if getattr(consult_backend, "uses_workdir", False):
            consult_workdir = await _prepare_workdir(
                _resolve_profile_workdir(profile, settings),
                stream,
                source=f"consult:{name}",
                language=language,
            )
        consult_request = AgentLoopRequest(
            prompt=question,
            session_id=consult_session_id(request, str(profile.get("id"))),
            language=language,
            workdir=consult_workdir,
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


def _resolve_profile_workdir(profile: dict[str, Any], settings: dict[str, Any]) -> tuple[str, str]:
    """``(resolved path, refused path)`` for one profile's configured workdir.

    A workdir outside the block's ``allowed_workdir_roots`` is refused: the CLI
    family runs with the server's privileges, so an unchecked path would be an
    arbitrary-directory grant. Roots resolve against the *admin* scope, not the
    requesting user's, because the allowlist is deployment config — a relative
    root must mean the same directory at save time and at every user's turn.
    """
    configured = str(profile.get("workdir") or "").strip()
    if not configured:
        return "", ""
    try:
        from kagweb.multi_user.paths import get_admin_path_service
        from kagweb.services.agent_loop.workdir import resolve_allowed_workdir

        resolved = resolve_allowed_workdir(
            configured,
            list(settings.get("allowed_workdir_roots") or []),
            base=get_admin_path_service().project_root,
        )
    except Exception:
        # An unusable base must not grant the directory.
        return "", configured
    if resolved is None:
        return "", configured
    return str(resolved), ""


async def _prepare_workdir(
    resolved: tuple[str, str],
    stream,  # noqa: ANN001
    *,
    source: str,
    language: str,
) -> str:
    """Create the resolved workdir, reporting a refusal or a failure.

    Returns the directory to run in, or "" to leave the backend on its default.
    """
    path, refused = resolved
    if refused:
        await stream.progress(
            t("agent_loop.workdir_not_allowed", path=refused, language=language),
            source=source,
            stage="responding",
        )
        return ""
    if not path:
        return ""
    try:
        Path(path).mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        # Distinct from "refused": the operator configured an allowed path that
        # cannot be created, which they can only fix if they can see it.
        await stream.progress(
            t("agent_loop.workdir_unusable", path=path, error=str(exc), language=language),
            source=source,
            stage="responding",
        )
        return ""
    return path


def _build_request(
    context: UnifiedContext,
    *,
    session_workspace: bool,
    workdir: str = "",
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

    # The operator workdir arrives already resolved, allowlisted and created
    # (see _prepare_workdir); only the session workspace is created here.
    resolved_workdir = workdir
    if not resolved_workdir and session_workspace and context.session_id:
        try:
            from kagweb.services.path_service import get_path_service

            workdir_path = get_path_service().get_task_workspace("chat", context.session_id)
            # get_task_workspace is a pure path join — create it here or the
            # subprocess spawn fails with a missing cwd on the first turn.
            workdir_path.mkdir(parents=True, exist_ok=True)
            resolved_workdir = str(workdir_path)
        except Exception:
            # A missing workspace must degrade to the server cwd, never
            # fail the turn.
            resolved_workdir = ""

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
        workdir=resolved_workdir,
    )


class _AgentLoopRoundBridge:
    """Forward agent-loop events with the trace metadata the UI reads.

    The external loop speaks a vendor-neutral vocabulary (``thinking``,
    ``content``, ``tool_call``, …) and knows nothing about KAGWeb's trace
    contract, so every event used to reach the frontend untagged. Tool rows
    fell back to the stage name (``"Responding"``) because neither ``call_kind``
    nor ``trace_group`` marked them as calls, and thinking carried no
    ``call_id`` at all — ``groupTraceEvents`` groups by that key, so the model's
    whole reasoning stream was dropped before it could render.

    This bridge restores the contract the native chat loop emits: a run of
    thinking/prose is one ``agent_loop_round`` sub-trace, closed by the tool
    call that ends it, and each tool call/result becomes its own ``tool_call``
    sub-trace.

    Rounds close with ``call_role="round"``, not ``"narration"``. A CLI prints
    the loop's prose inline, so it is part of the answer here — the frontend
    must keep it in the message bubble rather than demote it to trace-only
    text (a ``"narration"`` marker would strip it from the bubble). The turn's
    final pass closes its last round with ``"finish"`` — the signal that lets
    the activity trace settle once the loop is done — while a pass that is only
    the run-up to a consult closes with ``"round"``.

    State lives for one pass. A round stays open until a tool call ends it;
    tool ids the backend omits (custom backends never send one) are minted and
    paired FIFO so a result still attaches to its call.
    """

    def __init__(self, stream, *, source: str, stage: str) -> None:  # noqa: ANN001
        self.stream = stream
        self.source = source
        self.stage = stage
        self._round_id = ""
        self._round_trace: dict[str, Any] = {}
        self._last_round_trace: dict[str, Any] = {}
        # (minted id, tool name) for calls the backend sent without an id.
        self._pending_ids: deque[tuple[str, str]] = deque()
        self._tool_names: dict[str, str] = {}
        # Monotonic start per open tool call, for the backend-authoritative
        # ``elapsed_ms`` the trace and DSL prefer over timestamp spans.
        self._tool_started: dict[str, float] = {}

    # ---- rounds -------------------------------------------------------

    async def _emit_status(
        self,
        trace: dict[str, Any],
        state: str,
        *,
        role: str | None = None,
        text: str = "",
        usage: dict[str, Any] | None = None,
    ) -> None:
        """Emit one ``call_status`` marker for *trace*'s call."""
        extra: dict[str, Any] = {"trace_kind": "call_status", "call_state": state}
        if role is not None:
            extra["call_role"] = role
        if usage:
            extra.update(usage)
        await self.stream.progress(
            text,
            source=self.source,
            stage=self.stage,
            metadata=merge_trace_metadata(trace, extra),
        )

    async def _open_round(self) -> dict[str, Any]:
        """Start this pass's current round, or return the open one."""
        if self._round_id:
            return self._round_trace
        round_id = new_call_id("chat-round")
        trace = build_trace_metadata(
            call_id=round_id,
            phase=self.stage,
            label="Exploring",
            call_kind="agent_loop_round",
            trace_id=round_id,
            trace_role="explore",
            trace_group="stage",
        )
        self._round_id = round_id
        self._round_trace = trace
        await self._emit_status(trace, "running", text="Exploring")
        return trace

    async def _close_round(self, role: str, usage: dict[str, Any] | None = None) -> None:
        round_id, trace = self._round_id, self._round_trace
        self._round_id, self._round_trace = "", {}
        if not round_id:
            return
        self._last_round_trace = trace
        await self._emit_status(trace, "complete", role=role, usage=usage)

    # ---- pass lifecycle -----------------------------------------------

    async def content(self, text: str) -> None:
        """Stream one answer block as part of the current round."""
        if not text:
            return
        trace = await self._open_round()
        await self.stream.content(
            text,
            source=self.source,
            stage=self.stage,
            metadata=merge_trace_metadata(trace, {"trace_kind": "llm_chunk"}),
        )

    async def finish(self, *, terminal: bool, usage: dict[str, Any] | None = None) -> None:
        """Settle this pass's trace.

        ``terminal`` marks the turn's last pass: only it may read as the final
        answer. A non-terminal pass closes its open round as an intermediate
        one and stops there — the next pass carries the turn's answer, and
        settling as final would fold the activity trace mid-turn.

        ``usage`` is the pass's own counters. They ride the closing marker
        because KAGWeb cannot attribute a token to a round inside the external
        loop — the scope label on the marker says exactly that.
        """
        normalized = _normalize_usage(usage or {})
        if self._round_id:
            await self._close_round("finish" if terminal else "round", usage=normalized)
            return
        if not terminal or not self._last_round_trace:
            return
        # The pass ended on a tool result, so its call already closed the round;
        # the terminal marker lands on that last round group instead.
        await self._emit_status(
            self._last_round_trace, "complete", role="finish", usage=normalized
        )

    # ---- event forwarding ---------------------------------------------

    async def forward(self, event: AgentLoopEvent) -> None:
        if event.kind == "content":
            await self.content(event.text)
        elif event.kind == "thinking":
            await self._thinking(event)
        elif event.kind == "tool_call":
            await self._tool_call(event)
        elif event.kind == "tool_result":
            await self._tool_result(event)
        elif event.kind == "progress":
            if event.text:
                await self.stream.progress(event.text, source=self.source, stage=self.stage)
        elif event.kind == "error":
            # Mid-stream problems are surfaced in the trace; the turn still
            # completes with whatever answer the loop produced (matches how
            # native LLM error payloads stream as content).
            await self.stream.error(event.text, source=self.source, stage=self.stage)

    async def _thinking(self, event: AgentLoopEvent) -> None:
        # Empty thinking blocks are common in real streams and would open a
        # round holding nothing (the CLI prints a phantom "thinking…" for it).
        if not event.text.strip():
            return
        trace = await self._open_round()
        await self.stream.thinking(
            event.text,
            source=self.source,
            stage=self.stage,
            metadata=merge_trace_metadata(trace, {"trace_kind": "llm_chunk"}),
        )

    def _tool_trace(self, call_id: str, name: str, trace_kind: str, state: str) -> dict[str, Any]:
        """The tool-row contract: one ``tool_call`` sub-trace per call."""
        return build_trace_metadata(
            call_id=call_id,
            phase=self.stage,
            label=name,
            call_kind="tool_planning",
            trace_id=call_id,
            trace_role="tool",
            trace_group="tool_call",
            trace_kind=trace_kind,
            tool_name=name,
            call_state=state,
        )

    async def _tool_call(self, event: AgentLoopEvent) -> None:
        await self._close_round("round")
        name = event.name or "tool"
        args = event.data.get("args") if isinstance(event.data.get("args"), dict) else None
        call_id = str(event.data.get("id") or "")
        if not call_id:
            call_id = new_call_id("chat-tool")
            self._pending_ids.append((call_id, name))
        self._tool_names[call_id] = name
        self._tool_started[call_id] = time.monotonic()
        metadata = self._tool_trace(call_id, name, "tool_call", "running")
        if event.text:
            metadata["text"] = event.text
        await self.stream.tool_call(
            name,
            args if args is not None else {"input": event.text},
            source=self.source,
            stage=self.stage,
            metadata=metadata,
        )

    async def _tool_result(self, event: AgentLoopEvent) -> None:
        call_id = str(event.data.get("id") or "")
        name = event.name or ""
        # A result pairs with a minted call whenever its own id cannot: either
        # it carries none, or it carries one for a call we never saw (a backend
        # that ids the result but not the call). Without this the call row would
        # stay "running" forever while its result opened a second row.
        if self._pending_ids and (not call_id or call_id not in self._tool_names):
            call_id, minted_name = self._pending_ids.popleft()
            name = name or minted_name
        elif not call_id:
            # A result with no call ahead of it (a resumed turn, or a backend
            # that names nothing): still one row, never a crash.
            call_id = new_call_id("chat-tool")
        name = name or self._tool_names.get(call_id) or "tool"
        is_error = event.data.get("is_error")
        metadata = self._tool_trace(
            call_id, name, "tool_result", "error" if is_error else "complete"
        )
        if is_error is not None:
            metadata["is_error"] = bool(is_error)
        started = self._tool_started.pop(call_id, None)
        if isinstance(started, float):
            # Backend-authoritative duration: the client's timestamp span is
            # fragile across reconnects and replay, and the two clocks differ.
            metadata["elapsed_ms"] = int(round((time.monotonic() - started) * 1000))
        await self.stream.tool_result(
            name,
            event.text,
            source=self.source,
            stage=self.stage,
            metadata=metadata,
        )
        # Observation excerpt: the key bit of what the tool saw, as its own
        # event so the collapsed row can show it without expanding the result.
        # Mechanically derived and truncated — never an LLM-style summary, and
        # skipped entirely when the result is empty.
        excerpt = event.text.strip()
        if excerpt:
            if len(excerpt) > _OBSERVATION_EXCERPT_LIMIT:
                excerpt = excerpt[:_OBSERVATION_EXCERPT_LIMIT].rstrip() + "…"
            await self.stream.observation(
                excerpt,
                source=self.source,
                stage=self.stage,
                metadata=self._tool_trace(call_id, name, "observation", metadata["call_state"]),
            )


__all__ = ["ChatCapability"]
