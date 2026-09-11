"""Transport-neutral contract between KAGWeb turns and agent-loop backends.

The conversation backend is not a single LLM call: a turn is delegated to an
**agent loop** — an external runtime that plans, calls tools and streams its
work back. KAGWeb ships adapters for two transport families:

* **CLI backends** — terminal agent CLIs that accept a prompt and emit
  newline-delimited JSON on stdout (Claude Code, Codex, OpenCode, …).
  One subprocess per turn.
* **HTTP backends** — agent services exposing a streaming conversation
  endpoint (Intellect community/enterprise, Hermes, AgentScope, …), reached
  over POST with an SSE or NDJSON response.

Backends translate their vendor wire format into the neutral
:class:`AgentLoopEvent` schema defined here, so capabilities and the
frontend never see vendor-specific shapes.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

#: Hard cap on a single NDJSON/SSE line from any backend family (agents can
#: inline big tool outputs; an unbounded line is a memory-exhaustion vector).
MAX_LINE_BYTES = 4 * 1024 * 1024

#: Event kinds produced by every backend, whatever the vendor wire format.
#: ``approval_request`` / ``clarify_request`` are *request* kinds: they pause
#: the turn until a decision arrives through the backend's control methods
#: (``respond_approval`` / ``respond_clarify``). Only backends with
#: ``supports_control`` may emit them; other backends' request-shaped events
#: degrade to progress notes.
EVENT_KINDS = frozenset(
    {
        "approval_request",
        "clarify_request",
        "content",
        "thinking",
        "tool_call",
        "tool_result",
        "progress",
        "usage",
        "error",
    }
)

#: Choices a standard approval request carries. Backends may send their own
#: ``choices`` list; these are the vocabulary the UI and the settings default
#: understand.
APPROVAL_CHOICES = ("once", "session", "always", "deny")


@dataclass
class AgentLoopEvent:
    """One neutral progress event from an agent loop.

    ``text`` is the human-readable payload; ``name`` carries the tool name
    for ``tool_call`` / ``tool_result``; ``data`` holds structured extras
    (usage counters, progress numerators, vendor metadata) and is safe to
    drop for consumers that only render text.
    """

    kind: str
    text: str = ""
    name: str = ""
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentLoopRequest:
    """A single user turn handed to a backend.

    ``prompt`` is the complete user-side input (persona / grounding blocks
    already folded in by the caller); ``history`` is the prior conversation
    in OpenAI message shape — the HTTP family sends it as its own field, the
    CLI family folds it into the prompt because argv is its only input
    channel; ``workdir`` is a per-session working directory for backends
    that create files.
    """

    prompt: str
    history: list[dict[str, Any]] = field(default_factory=list)
    session_id: str = ""
    language: str = "en"
    workdir: str = ""
    #: Per-turn model override resolved from the user's ``llm_selection``.
    #: Empty means "backend default" (the profile's configured model, or the
    #: backend's own default). Family support varies — the CLI one-shot family
    #: substitutes it into ``{model}``, the HTTP runs/turn body carries a
    #: ``model`` key (consumed upstream where supported), and the ACP surface
    #: has no per-turn model field at all, so ``acp`` ignores it.
    model: str = ""


class AgentLoopError(RuntimeError):
    """Terminal backend failure; fails the turn with this message."""

    def __init__(self, message: str, *, backend: str = ""):
        super().__init__(message)
        self.backend = backend


class AgentLoopBackend(ABC):
    """One configured agent-loop backend.

    Implementations are cheap per-turn objects: ``run`` is an async
    generator that yields events as the loop works and either exhausts
    (turn complete) or raises :class:`AgentLoopError` (turn failed).
    Cancellation (the executor cancelling the turn task) must terminate the
    underlying process / connection in a ``finally`` block.
    """

    name: str = ""

    #: Whether this family runs with ``request.workdir`` as its working
    #: directory. The CLI family spawns a subprocess and does; an HTTP service
    #: runs in the operator's own process and ignores it, so callers must not
    #: create a directory or enforce the workdir allowlist on its behalf.
    uses_workdir: bool = False

    #: Whether the backend can pause mid-turn on an ``approval_request``
    #: event and resume on :meth:`respond_approval`. The capability only
    #: parks the turn for backends that declare this; everyone else's
    #: request-shaped events degrade to progress notes. Backends that own a
    #: live connection (ACP, run-style HTTP) set this; one-shot CLI
    #: subprocesses cannot.
    supports_control: bool = False

    @abstractmethod
    def run(self, request: AgentLoopRequest) -> Any:  # pragma: no cover - ABC
        """Yield :class:`AgentLoopEvent` objects for one turn."""
        raise NotImplementedError

    async def respond_approval(self, request_id: str, choice: str) -> None:
        """Deliver the user's decision for one pending ``approval_request``.

        ``choice`` is one of the choices the request carried (by default
        :data:`APPROVAL_CHOICES`). Only called on backends with
        ``supports_control``; the default raises so a wiring bug surfaces
        instead of silently dropping the decision.
        """
        raise NotImplementedError(f"{type(self).__name__} does not support approvals")

    async def respond_clarify(self, request_id: str, answer: str) -> None:
        """Deliver the user's answer for one pending ``clarify_request``."""
        raise NotImplementedError(f"{type(self).__name__} does not support clarifications")

    async def cancel(self) -> None:
        """Interrupt the in-flight turn without tearing the backend down.

        Distinct from cancelling the ``run`` generator (which the executor
        already does): a session-scoped backend keeps its connection alive
        across turns, so a mid-turn stop must reach the agent as a control
        message instead of killing the transport.
        """
        raise NotImplementedError(f"{type(self).__name__} does not support cancellation")


__all__ = [
    "APPROVAL_CHOICES",
    "AgentLoopBackend",
    "AgentLoopError",
    "AgentLoopEvent",
    "AgentLoopRequest",
    "EVENT_KINDS",
    "MAX_LINE_BYTES",
]
