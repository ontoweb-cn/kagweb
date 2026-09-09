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
from pathlib import Path
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
            answer, pass_usage = await self._run_single_pass(
                stream, backend, request, context=context, profile=primary, language=language
            )
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
        *,
        context: UnifiedContext,
        profile: dict[str, Any],
        language: str,
    ) -> tuple[str, dict[str, Any]]:
        """Stream one backend pass; return (answer text, usage counters)."""
        answer_parts: list[str] = []
        usage: dict[str, Any] = {}
        async for event in backend.run(request):
            if event.kind == "approval_request" and getattr(backend, "supports_control", False):
                await self._handle_approval_request(
                    context, stream, backend, event, profile, language
                )
                continue
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
        tool = event.name or str(data.get("tool") or "tool")
        preview = event.text or str(data.get("preview") or "")
        timeout = _approval_timeout(profile)
        default_choice = _approval_default_choice(profile, choices)

        question = _approval_question(
            tool=tool, preview=preview, choices=choices, language=language
        )
        await stream.tool_result(
            "ask_user",
            "",
            source=self.name,
            stage="responding",
            metadata={"tool_metadata": {"ask_user": {"questions": [question]}}},
        )

        waiter = getattr(getattr(context, "runtime", None), "wait_for_user_reply", None)
        reply: dict[str, Any] | None = None
        if waiter is not None and timeout > 0:
            try:
                reply = await asyncio.wait_for(waiter(), timeout=timeout)
            except asyncio.TimeoutError:
                reply = None
        choice = _approval_choice_from_reply(reply, question["options"], default_choice)

        await stream.progress(
            t("agent_loop.approval_decision", tool=tool, choice=choice, language=language),
            source=self.name,
            stage="responding",
            metadata={
                "approval": {"request_id": request_id, "tool": tool, "decision": choice},
            },
        )
        if request_id:
            await backend.respond_approval(request_id, choice)

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


async def _emit_agent_loop_event(
    stream, event: AgentLoopEvent, *, source: str, stage: str
) -> None:  # noqa: ANN001
    if event.kind == "content":
        await stream.content(event.text, source=source, stage=stage)
    elif event.kind == "thinking":
        await stream.thinking(event.text, source=source, stage=stage)
    elif event.kind in {"approval_request", "clarify_request"}:
        # A request-shaped event from a backend without control support can
        # never be answered — surface it as a note instead of dropping it so
        # the trace shows why the agent kept (or failed to keep) going.
        tool = event.name or str((event.data or {}).get("tool") or "")
        await stream.progress(
            event.text or t("agent_loop.request_unsupported", kind=event.kind, tool=tool),
            source=source,
            stage=stage,
            metadata={"kind": event.kind},
        )
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


def _approval_timeout(profile: dict[str, Any]) -> int:
    try:
        value = int(profile.get("approval_timeout_seconds", 60))
    except (TypeError, ValueError):
        return 60
    return max(_APPROVAL_TIMEOUT_RANGE[0], min(_APPROVAL_TIMEOUT_RANGE[1], value))


def _approval_default_choice(profile: dict[str, Any], choices: list[str]) -> str:
    value = str(profile.get("approval_default") or "deny").strip().lower()
    if value not in _APPROVAL_DEFAULTS:
        value = "deny"
    # A backend that did not offer the configured fallback falls back to deny
    # rather than answering something the agent would not understand.
    return value if value in choices else "deny"


def _approval_question(
    *, tool: str, preview: str, choices: list[str], language: str
) -> dict[str, Any]:
    """One ``ask_user`` question shaped for the existing card renderers."""
    prompt = t("agent_loop.approval_prompt", tool=tool, language=language)
    if preview:
        prompt = f"{prompt}\n{preview}"
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


__all__ = ["ChatCapability"]
