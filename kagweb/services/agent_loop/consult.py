"""The consult protocol: how the primary agent loop asks for a second opinion.

KAGWeb delegates whole turns to the primary agent loop, so — unlike
DeepMentor, whose in-process LLM calls ``consult_subagent`` as a real tool —
the primary here is an external process/service with no way to call back
into KAGWeb mid-turn. The adaptation keeps the same shape (the *loop*
decides when to consult, with a per-turn budget) while riding entirely on
the existing request/response-stream contract:

1. When consultable profiles exist, KAGWeb appends a **manifest** to the
   turn prompt listing them and the directive format.
2. A backend that wants a consultation ends its reply with **only** a
   fenced ``consult`` block naming the agent and the question.
3. KAGWeb parses the tail directive, runs the named profile as a consult
   (its events stream as progress under ``consult:<name>``; the exchange is
   emitted as ``consult_agent`` tool_call/tool_result so the trace reads
   like any tool consult), appends the result to the history, and re-runs
   the primary — up to ``consult_budget`` consults per turn.

Consult sessions use ``<chat session>::consult::<profile id>`` as their
session id, so backends that keep session state answer follow-up consults
with continuity — DeepMentor's cross-turn session registry, expressed
through the session id our contract already carries.
"""

from __future__ import annotations

import json
import re
from dataclasses import replace
from typing import Any

from kagweb.services.agent_loop.protocol import AgentLoopRequest
from kagweb.services.i18n import t

#: Fence word the primary must use to request a consultation.
CONSULT_FENCE = "consult"

_DIRECTIVE_RE = re.compile(
    rf"```{CONSULT_FENCE}[ \t]*\r?\n(.*?)\r?\n```[ \t]*\r?\n?$",
    re.DOTALL | re.IGNORECASE,
)


def consult_manifest(profiles: list[dict[str, Any]], *, budget: int, language: str) -> str:
    """The prompt block advertising consultable agents and the directive."""
    listed = "\n".join(
        f"- {profile.get('id')} ({profile.get('name')}): {profile.get('preset')}"
        for profile in profiles
    )
    return t(
        "agent_loop.consult_manifest",
        agents=listed,
        fence=CONSULT_FENCE,
        budget=budget,
        language=language,
    )


def parse_consult_directive(answer: str) -> dict[str, str] | None:
    """Extract a trailing consult directive, or ``None``.

    Only the *tail* counts — a fenced block quoted mid-answer for display
    must never trigger a consultation. Malformed JSON parses to ``None``
    (the turn then completes with the answer as spoken).
    """
    match = _DIRECTIVE_RE.search(str(answer or "").rstrip())
    if match is None:
        return None
    try:
        payload = json.loads(match.group(1))
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    agent = str(payload.get("agent") or "").strip()
    question = str(payload.get("question") or "").strip()
    if not agent or not question:
        return None
    return {"agent": agent, "question": question}


def strip_consult_directive(answer: str) -> str:
    """Remove the trailing consult block from an answer."""
    return _DIRECTIVE_RE.sub("", str(answer or "").rstrip()).rstrip()


def consult_session_id(request: AgentLoopRequest, profile_id: str) -> str:
    base = str(request.session_id or "session")
    return f"{base}::consult::{profile_id}"


def followup_request(
    request: AgentLoopRequest,
    *,
    pass_answer: str,
    consult_name: str,
    consult_answer: str,
    language: str,
) -> AgentLoopRequest:
    """Build the next pass's request: the consult result rides in history."""
    history = [dict(item) for item in request.history]
    history.append(
        {"role": "assistant", "content": strip_consult_directive(pass_answer) or "(consulting)"}
    )
    history.append(
        {"role": "user", "content": f"[consult:{consult_name}]\n{consult_answer}"}
    )
    prompt = t("agent_loop.consult_followup", language=language)
    return replace(request, prompt=prompt, history=history)


__all__ = [
    "CONSULT_FENCE",
    "consult_manifest",
    "consult_session_id",
    "followup_request",
    "parse_consult_directive",
    "strip_consult_directive",
]
