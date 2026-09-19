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

import asyncio
from collections import deque
import logging
import math
from pathlib import Path
import time
from typing import Any

from kagweb.capabilities._shared import emit_capability_result
from kagweb.core.capability_protocol import CapabilityManifest, TurnCapability
from kagweb.core.context import UnifiedContext
from kagweb.core.trace import build_trace_metadata, merge_trace_metadata, new_call_id
from kagweb.services.agent_loop import build_agent_loop_backend
from kagweb.services.agent_loop.builtin import preset_family
from kagweb.services.agent_loop.consult import (
    consult_manifest,
    consult_session_id,
    followup_request,
    parse_consult_directive,
    strip_consult_directive,
)
from kagweb.services.agent_loop.identity import (
    IdentityUnavailable,
    resolve_backend_identity,
)
from kagweb.services.agent_loop.protocol import (
    APPROVAL_CHOICES,
    AgentLoopBackend,
    AgentLoopEvent,
)
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
from kagweb.services.kag.trace import kag_trace_metadata
from kagweb.services.llm.usage_tracker import UsageTracker
from kagweb.services.settings.interface_settings import get_response_language
from kagweb.utils.text_display import first_line_label, untruncated_stem

logger = logging.getLogger(__name__)

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

    Only reported numbers survive: a counter the backend omitted is left out
    rather than zero-filled, so a partial pair cannot masquerade as a measured
    zero. ``total`` is written when the backend sent one, or when a reported
    pair is explicitly a whole-pass figure; a ``cumulative`` pair (or one with
    no scope at all) ships without ``total``, because a synthesized sum would
    be a lie.
    """
    prompt = _as_count(usage.get("input_tokens", usage.get("prompt_tokens")))
    completion = _as_count(usage.get("output_tokens", usage.get("completion_tokens")))
    normalized: dict[str, Any] = {}
    if prompt is not None:
        normalized["prompt_tokens"] = prompt
    if completion is not None:
        normalized["completion_tokens"] = completion
    if not normalized:
        return {}
    scope = str(usage.get("usage_scope") or "")
    if scope:
        normalized["usage_scope"] = scope
    total = _as_count(usage.get("total_tokens"))
    if total is None and scope == "pass" and prompt is not None and completion is not None:
        total = prompt + completion
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
        # One settings read for THIS layer: load_system() hits the JSON file
        # (and may rewrite it), so the backend factory, the request builder and
        # the consult resolver all reuse this block rather than going to disk
        # again. The turn gate in the request preparer reads it separately — it
        # runs before this capability is entered and needs the block to decide
        # what the turn requires.
        settings = get_agent_loop_settings()
        primary = resolve_primary_profile(settings)
        backend = build_agent_loop_backend(primary) if primary else None
        if backend is None:
            await self._run_shell_notice(context, stream)
            return
        await self._run_agent_loop(context, stream, backend, primary, settings)

    @staticmethod
    def _apply_identity(
        backend: AgentLoopBackend,
        profile: dict[str, Any],
        language: str,
    ) -> None:
        """Bind this turn to the calling account, when the profile asks.

        The factory resolves the deployment-wide credential from the profile;
        *who the turn belongs to* is per-user and only knowable here. A linked
        account whose credential no longer works is a hard failure by design —
        falling back to the service key would grant more reach than the token
        that just failed — so it surfaces as the turn's error rather than
        something to route around.

        Resolved with the turn's language so a failure message reads in the
        user's own language, which is why the capability does this rather than
        leaving it entirely to the backend's own fallback.
        """
        try:
            identity = resolve_backend_identity(
                profile,
                family=preset_family(str(profile.get("preset") or "")),
                language=language,
            )
        except IdentityUnavailable as exc:
            raise RuntimeError(str(exc)) from exc
        if identity is None:
            return
        # getattr, not a direct call: a duck-typed backend that predates the
        # hook (or the CLI/ACP family, which has no headers to carry) simply
        # keeps its own credential.
        apply = getattr(backend, "with_identity", None)
        if callable(apply):
            apply(identity)
        if identity.degraded:
            # Attribution worked but the account is not linked: the turn is
            # fine, it just cannot be delegated yet. Leave a trace so the UI can
            # offer connecting an account without failing the conversation.
            logger.info(
                "agent-loop: running without a linked Intellect account "
                "(session scope only, no service-side isolation)"
            )

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
        self._apply_identity(backend, primary, language)
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
            answer, pass_usage, bridge = await self._run_single_pass(
                stream,
                backend,
                request,
                context=context,
                profile=primary,
                language=language,
            )
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
        *,
        context: UnifiedContext,
        profile: dict[str, Any],
        language: str,
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
            if event.kind in {"approval_request", "clarify_request"} and getattr(
                backend, "supports_control", False
            ):
                # The card is its own trace unit, and the frontend splits the
                # trace at it: settle the open round first so the rounds before
                # the card stay above it and the resumed round opens a fresh
                # group below, instead of one group straddling the card.
                await bridge.finish(terminal=False)
                if event.kind == "approval_request":
                    await self._handle_approval_request(
                        context, stream, backend, event, profile, language
                    )
                else:
                    await self._handle_clarify_request(
                        context, stream, backend, event, profile, language
                    )
                continue
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

    async def _handle_approval_request(
        self,
        context: UnifiedContext,
        stream,  # noqa: ANN001
        backend: AgentLoopBackend,
        event: AgentLoopEvent,
        profile: dict[str, Any],
        language: str,
    ) -> None:
        """Park the turn on one ``approval_request`` until a decision lands.

        The request surfaces as an ``ask_user``-shaped card (options chips on
        the web, the inline question prompt in the CLI), the turn moves to
        ``waiting_input`` through the runtime reply queue, and the answer —
        or the profile's fallback policy on timeout / a headless entry point
        — goes back to the backend via ``respond_approval``. The backend owns
        the actual pause: its ``run`` generator simply does not advance until
        the decision arrives.
        """
        data = event.data if isinstance(event.data, dict) else {}
        request_id = str(data.get("request_id") or "")
        choices = [str(choice) for choice in (data.get("choices") or APPROVAL_CHOICES)]
        name = event.name or str(data.get("tool") or "tool")
        tool = first_line_label(name, limit=80) or "tool"
        # The label above is clamped for the sentence; the body must still
        # disclose what would actually run. A backend that names the action
        # in ``name`` and sends no separate detail (the ACP raw_input-only
        # case) would otherwise approve an action the user never saw.
        detail = event.text or str(data.get("preview") or "") or name
        timeout = _approval_timeout(profile, backend, kind="approval", language=language)
        default_choice = _approval_default_choice(profile, choices)

        question = _approval_question(tool=tool, detail=detail, choices=choices, language=language)
        # The same ask_user shape the native tool uses, on both channels the
        # clients already speak: the web renders its card from the tool_call
        # args (TracePresentation), the CLI intercepts the tool_result
        # metadata for the inline prompt. call_id pairs the two in the trace.
        call_id = f"approval-{request_id or id(event)}"
        await stream.tool_call(
            "ask_user",
            {"questions": [question]},
            source=self.name,
            stage="responding",
            metadata={"call_id": call_id, "call_state": "running"},
        )
        await stream.tool_result(
            "ask_user",
            "",
            source=self.name,
            stage="responding",
            metadata={
                "call_id": call_id,
                "tool_metadata": {"ask_user": {"questions": [question]}},
            },
        )

        waiter = getattr(getattr(context, "runtime", None), "wait_for_user_reply", None)
        reply: dict[str, Any] | None = None
        if waiter is not None and timeout > 0:
            try:
                reply = await asyncio.wait_for(waiter(), timeout=timeout)
            except asyncio.TimeoutError:
                reply = None
            except asyncio.CancelledError:
                # The waiter raises a synthetic CancelledError when the turn
                # is not tracked by the executor (status transition failed).
                # Only re-raise when THIS task is genuinely being cancelled —
                # otherwise degrade to the policy answer like a timeout.
                if asyncio.current_task().cancelling():
                    raise
                reply = None
        choice = _approval_choice_from_reply(reply, question["options"], default_choice)

        await stream.progress(
            t("agent_loop.approval_decision", tool=tool, choice=choice, language=language),
            source=self.name,
            stage="responding",
            metadata={
                "approval": {"request_id": request_id, "tool": tool, "decision": choice},
                # The same resolution marker the web's ask_user card renderer
                # consumes: it flips the pending card to its answered state.
                "ask_user_resolved": True,
                "ask_user_tool_call_id": call_id,
                "answers": [{"questionId": "approval", "text": choice}],
            },
        )
        if request_id:
            await backend.respond_approval(request_id, choice)

    async def _handle_clarify_request(
        self,
        context: UnifiedContext,
        stream,  # noqa: ANN001
        backend: AgentLoopBackend,
        event: AgentLoopEvent,
        profile: dict[str, Any],
        language: str,
    ) -> None:
        """Ask the user one question mid-turn, then hand the answer back.

        This is the same shape of interaction as an approval — the backend's
        generator parks until a reply arrives, and the card rides the clients'
        existing ``ask_user`` rendering — but the two differ in what they ask.
        An approval wants a bounded decision and falls back to a policy on
        silence (``approval_default``); a clarify wants free text and has no
        policy answer, so a timeout resolves it as skipped rather than
        inventing a reply the agent would act on.
        """
        data = event.data if isinstance(event.data, dict) else {}
        request_id = str(data.get("request_id") or "")
        prompt = event.text or str(data.get("question") or "")
        choices = [str(choice) for choice in (data.get("choices") or [])]
        timeout = _approval_timeout(profile, backend, kind="clarify", language=language)

        question = _clarify_question(prompt=prompt, choices=choices, language=language)
        call_id = f"clarify-{request_id or id(event)}"
        await stream.tool_call(
            "ask_user",
            {"questions": [question]},
            source=self.name,
            stage="responding",
            metadata={"call_id": call_id, "call_state": "running"},
        )
        await stream.tool_result(
            "ask_user",
            "",
            source=self.name,
            stage="responding",
            metadata={
                "call_id": call_id,
                "tool_metadata": {"ask_user": {"questions": [question]}},
            },
        )

        waiter = getattr(getattr(context, "runtime", None), "wait_for_user_reply", None)
        reply: dict[str, Any] | None = None
        if waiter is not None and timeout > 0:
            try:
                reply = await asyncio.wait_for(waiter(), timeout=timeout)
            except asyncio.TimeoutError:
                reply = None
            except asyncio.CancelledError:
                if asyncio.current_task().cancelling():
                    raise
                reply = None
        answer = _clarify_answer_from_reply(reply)

        await stream.progress(
            t("agent_loop.clarify_decision", language=language)
            if answer
            else t("agent_loop.clarify_skipped", language=language),
            source=self.name,
            stage="responding",
            metadata={
                "clarify": {"request_id": request_id, "answer": answer},
                # The same resolution marker the web's ask_user card renderer
                # consumes: it flips the pending card to its answered state.
                "ask_user_resolved": True,
                "ask_user_tool_call_id": call_id,
                "answers": [{"questionId": "clarify", "text": answer}],
            },
        )
        if request_id and getattr(backend, "supports_control", False):
            await backend.respond_clarify(request_id, answer)

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
        # A consult runs as the same account as the turn that asked for it, so
        # it carries the same identity. A broken link fails the consult (which
        # degrades to a note) rather than silently borrowing the service key.
        try:
            self._apply_identity(consult_backend, profile, language)
        except RuntimeError as exc:
            await stream.error(str(exc), source=self.name, stage="responding")
            return str(exc)
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
    kag_settings: dict[str, Any] = {}
    try:
        from kagweb.services.kag import get_kag_settings, kag_grounding_block

        # 一次读取复用：grounding 块与 .mcp.json 共用本 turn 的 kag 块
        # （capability 对 agent_loop 的 "one settings read" 原则同样适用）
        kag_settings = get_kag_settings()
        kag_block = kag_grounding_block(kag_settings)
    except Exception:
        kag_block = ""  # 可选集成：KAG 服务异常不阻塞 turn
    blocks = [
        block.strip()
        for block in (
            context.sidebar_context,
            f"Attached sources:\n{context.source_manifest}" if context.source_manifest else "",
            consult_manifest or "",
            kag_block,
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
    if resolved_workdir:
        # KAG 接线：CLI 后端经 session workdir 的 .mcp.json 发现 bridge
        # （设计 §5.3）。可选集成——写入失败仅意味着本 turn 无 KAG 工具。
        try:
            from kagweb.services.kag import ensure_session_mcp_config

            ensure_session_mcp_config(resolved_workdir, str(context.session_id or ""), kag_settings)
        except Exception:
            pass

    history = [
        item
        for item in context.conversation_history
        if isinstance(item, dict)
        and str(item.get("role") or "") in {"user", "assistant", "system"}
        and item.get("content")
    ]
    # Per-turn model override: the turn's grant-validated llm_selection
    # resolves to the concrete model name the backend should run (CLI {model}
    # substitution / HTTP body key). An empty result keeps the backend on its
    # configured/default model.
    turn_model = ""
    selection = context.metadata.get("llm_selection") or {}
    if selection:
        try:
            from kagweb.services.model_selection import resolve_agent_model_for_selection

            turn_model = resolve_agent_model_for_selection(selection)
        except Exception:
            turn_model = ""
    return AgentLoopRequest(
        prompt=prompt,
        history=history,
        session_id=context.session_id,
        language=context.language or "en",
        workdir=resolved_workdir,
        model=turn_model,
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
        # Call args by id, kept only until the paired result consumes them:
        # KAG 轨迹归一需要 kag_solve 的 question 作 query（fold 层读
        # result 的 metadata.query），不进持久化事件。
        self._tool_args: dict[str, dict[str, Any]] = {}
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
        if not self._last_round_trace:
            return
        if not terminal and not normalized:
            # Nothing to add: an intermediate pass whose round already closed
            # (the last event was a tool result) would only duplicate a marker.
            return
        # The pass ended on a tool result, so its call already closed the round;
        # the marker lands on that last round group instead. A non-terminal
        # pass keeps its intermediate role — only the turn's last pass may
        # read as the finish.
        await self._emit_status(
            self._last_round_trace,
            "complete",
            role="finish" if terminal else "round",
            usage=normalized,
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
        elif event.kind in {"approval_request", "clarify_request"}:
            # A request-shaped event from a backend without control support can
            # never be answered — surface it as a note instead of dropping it so
            # the trace shows why the agent kept (or failed to keep) going.
            tool = event.name or str((event.data or {}).get("tool") or "")
            await self.stream.progress(
                event.text or t("agent_loop.request_unsupported", kind=event.kind, tool=tool),
                source=self.source,
                stage=self.stage,
                metadata={"kind": event.kind},
            )
        elif event.kind == "error":
            # Mid-stream problems are surfaced in the trace; the turn still
            # completes with whatever answer the loop produced (matches how
            # native LLM error payloads stream as content). Any structured
            # extras ride along in the metadata so a client can act on the
            # machine-readable cause (an ACP ``stop_reason``, say) instead of
            # pattern-matching the prose.
            await self.stream.error(
                event.text,
                source=self.source,
                stage=self.stage,
                metadata=dict(event.data) if event.data else None,
            )

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
        if isinstance(args, dict) and args:
            self._tool_args[call_id] = args
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
        # KAG 轨迹归一（M3.2）：kag_solve 的 JSON 结果 → 前端活动面板的
        # graph/sources/query 元数据（services/kag/trace.py 完成形态转换；
        # 兼容 CLI 后端的 ``mcp__<server>__kag_solve`` 命名空间前缀，非 KAG
        # 工具或不可解析时零副作用）。
        kag_meta = kag_trace_metadata(
            name, self._tool_args.pop(call_id, None), event.text
        )
        if kag_meta:
            if kag_meta.get("query"):
                metadata["query"] = kag_meta["query"]
            if kag_meta.get("sources"):
                metadata["sources"] = kag_meta["sources"]
            if kag_meta.get("tool_metadata"):
                metadata["tool_metadata"] = kag_meta["tool_metadata"]
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
        excerpt = (kag_meta or {}).get("excerpt") or event.text.strip()
        if excerpt:
            if len(excerpt) > _OBSERVATION_EXCERPT_LIMIT:
                excerpt = excerpt[:_OBSERVATION_EXCERPT_LIMIT].rstrip() + "…"
            await self.stream.observation(
                excerpt,
                source=self.source,
                stage=self.stage,
                metadata=self._tool_trace(call_id, name, "observation", metadata["call_state"]),
            )


# ---------------------------------------------------------------------------
# Approval requests — the agent-loop flavour of ask_user
# ---------------------------------------------------------------------------

#: ``approval_timeout_seconds`` bounds how long a parked turn waits for a
#: decision before the fallback policy answers for the user.
_APPROVAL_TIMEOUT_RANGE = (5, 600)

#: Legal ``approval_default`` values — the policy answer used when no
#: interactive client replies in time. Anything else is refused at
#: normalization, so the runtime can trust this set.
_APPROVAL_DEFAULTS = frozenset({"deny", "once", "session", "always"})


def _approval_timeout(
    profile: dict[str, Any],
    backend: AgentLoopBackend | None = None,
    *,
    kind: str = "approval",
    language: str = "en",
) -> int:
    """How long to park the turn, clamped to what the backend can honour.

    The operator sets one window for both cards, but the service on the other
    end resolves each on its own timer — and it does not wait for us. A wait
    longer than the service's means the user's answer is delivered after the
    agent stopped listening, which reads as "I replied and nothing happened":
    the worst kind of failure, because it looks like the user's fault. So the
    declared ceiling wins and the difference is reported, not silently applied.
    """
    try:
        value = int(profile.get("approval_timeout_seconds", 60))
    except (TypeError, ValueError):
        value = 60
    value = max(_APPROVAL_TIMEOUT_RANGE[0], min(_APPROVAL_TIMEOUT_RANGE[1], value))
    if backend is None:
        return value
    attribute = "clarify_timeout_limit" if kind == "clarify" else "approval_timeout_limit"
    limit = getattr(backend, attribute, None)
    if not isinstance(limit, int) or limit <= 0 or value <= limit:
        return value
    logger.info(
        "agent-loop: %s wait clamped from %ss to %ss by backend %r",
        kind,
        value,
        limit,
        getattr(backend, "name", ""),
    )
    return limit


def _approval_default_choice(profile: dict[str, Any], choices: list[str]) -> str:
    value = str(profile.get("approval_default") or "deny").strip().lower()
    if value not in _APPROVAL_DEFAULTS:
        value = "deny"
    # A backend that did not offer the configured fallback falls back to deny
    # rather than answering something the agent would not understand.
    return value if value in choices else "deny"


def _detail_beyond_label(*, tool: str, detail: str) -> str:
    """The part of ``detail`` worth printing under the label, if any.

    Returns ``""`` only when the detail is a verbatim restatement of the
    label — the degenerate case where a backend hands over one string as
    both the name and the body, which would otherwise print twice.

    The comparison is whole-line, never a bare prefix: labels are short
    words (``read``, ``exec``, ``git``) that prefix unrelated text
    (``readme.md``, ``execution policy``, ``github.com``), and slicing on
    that overlap would silently cut the body's opening characters off.
    """
    text = str(detail or "").strip()
    label = untruncated_stem(str(tool or "").strip())
    if not text or not label:
        return text
    if text == label:
        return ""
    # The label is the body's own opening line (the clamped-title case).
    first, separator, rest = text.partition("\n")
    if first.strip() == label and separator:
        return rest.strip()
    return text


def _approval_question(
    *, tool: str, detail: str, choices: list[str], language: str
) -> dict[str, Any]:
    """One ``ask_user`` question shaped for the existing card renderers.

    ``tool`` names the action in the sentence and ``detail`` is the body
    shown under it. An approval must disclose what it will run, so a
    clamped ``tool`` is paired with the untruncated ``detail`` by the
    caller — this function only decides whether the body is worth printing.
    """
    prompt = t("agent_loop.approval_prompt", tool=tool, language=language)
    remainder = _detail_beyond_label(tool=tool, detail=detail)
    if remainder:
        prompt = f"{prompt}\n{remainder}"
    return {
        "id": "approval",
        "header": t("agent_loop.approval_header", language=language),
        "prompt": prompt,
        "options": [
            {
                "value": choice,
                "label": t(f"agent_loop.approval_choice_{choice}", language=language),
                "description": t(f"agent_loop.approval_choice_{choice}_hint", language=language),
            }
            for choice in choices
        ],
        "multi_select": False,
    }


def _approval_choice_from_reply(
    reply: dict[str, Any] | None,
    options: list[dict[str, Any]],
    default_choice: str,
) -> str:
    """Map an ``ask_user`` reply payload to one of the offered choices.

    The reply is whatever ``submit_user_reply`` put on the queue — v2
    ``answers`` pairs or a legacy free-form ``text``. Interactive clients
    echo back the option *label* (the CLI resolves ``1``-style picks to the
    printed label) or the raw value, so both are matched
    case-insensitively. Anything unrecognized — timeout, empty answer, a
    choice the backend never offered — resolves to the profile's fallback
    policy.
    """
    text = ""
    if isinstance(reply, dict):
        answers = reply.get("answers")
        if isinstance(answers, list) and answers and isinstance(answers[0], dict):
            text = str(answers[0].get("text") or "")
        if not text and isinstance(reply.get("text"), str):
            text = reply["text"]
    text = text.strip().lower()
    if not text:
        return default_choice
    for option in options:
        if not isinstance(option, dict):
            continue
        value = str(option.get("value") or "").strip().lower()
        label = str(option.get("label") or "").strip().lower()
        if text in {value, label}:
            return str(option.get("value") or default_choice)
    return default_choice


def _clarify_question(*, prompt: str, choices: list[str], language: str) -> dict[str, Any]:
    """One ``ask_user`` question for a mid-turn ``clarify``.

    Same card shape as an approval, but the text is the agent's own question —
    there is no fixed vocabulary to render — and free text is the primary way
    to answer, so the input stays open even when the agent suggested choices.
    """
    return {
        "id": "clarify",
        "header": t("agent_loop.clarify_header", language=language),
        "prompt": prompt or t("agent_loop.clarify_prompt", language=language),
        "options": [
            # The card renders options by ``label`` alone, so a suggestion has
            # to carry one; ``value`` is kept for the reply-matching path.
            {"value": choice, "label": choice, "description": None}
            for choice in choices
            if choice.strip()
        ],
        "multi_select": False,
        "allow_free_text": True,
        "placeholder": t("agent_loop.clarify_placeholder", language=language),
    }


def _clarify_answer_from_reply(reply: dict[str, Any] | None) -> str:
    """The user's free-text answer, or ``""`` when they did not answer.

    Unlike an approval there is no policy fallback: a clarify that goes
    unanswered must read as skipped, because any invented text would be acted
    on by the agent as if the user had said it.
    """
    if not isinstance(reply, dict):
        return ""
    answers = reply.get("answers")
    if isinstance(answers, list) and answers and isinstance(answers[0], dict):
        text = str(answers[0].get("text") or "")
        if text.strip():
            return text.strip()
    fallback = reply.get("text")
    return fallback.strip() if isinstance(fallback, str) else ""


__all__ = ["ChatCapability"]
