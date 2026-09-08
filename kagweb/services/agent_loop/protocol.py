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
EVENT_KINDS = frozenset(
    {
        "content",
        "thinking",
        "tool_call",
        "tool_result",
        "progress",
        "usage",
        "error",
    }
)


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

    @abstractmethod
    def run(self, request: AgentLoopRequest) -> Any:  # pragma: no cover - ABC
        """Yield :class:`AgentLoopEvent` objects for one turn."""
        raise NotImplementedError


__all__ = [
    "AgentLoopBackend",
    "AgentLoopError",
    "AgentLoopEvent",
    "AgentLoopRequest",
    "EVENT_KINDS",
    "MAX_LINE_BYTES",
]
