"""ACP-family agent-loop backend: one long-lived agent child per session.

Drives an Agent Client Protocol (ACP) agent — ``intellect acp`` for the
Intellect community edition — over stdio JSON-RPC using the
``agent-client-protocol`` SDK (extra ``.[acp]``). Unlike the one-shot CLI
family, the child is a **session-scoped asset**: it stays alive across
turns (the agent keeps its own session state, history and compression
chain), and only the ACP session is cancelled when a turn is interrupted.
The manager reaps idle children and a crashed child is respawned on the
next turn, re-attaching the persisted agent session via ``load_session``.

Wire mapping (ACP → neutral :class:`AgentLoopEvent`):

* ``AgentMessageChunk`` deltas buffer into one content block, flushed at
  tool/plan boundaries and at turn end (content events are whole blocks).
* ``AgentThoughtChunk`` → ``thinking``.
* ``ToolCallStart`` → ``tool_call``; ``ToolCallUpdate`` with
  ``completed``/``failed`` status → ``tool_result``.
* ``Plan`` → ``progress`` with the entry titles.
* ``request_permission`` → ``approval_request`` carrying an internally
  generated request id; :meth:`AcpAgentLoopBackend.respond_approval`
  resolves it and answers the agent with the closest matching permission
  option. Unresolved requests (turn cancelled) are denied.

Per-session children keep concurrent turns on disjoint connections; a
shared multiplexed child is a possible future economy, not a default.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
import logging
from pathlib import Path
import time
from typing import Any

from kagweb.services.i18n import t
from kagweb.utils.text_display import first_line_label

from .acp_session_store import forget_acp_session, load_acp_session, save_acp_session
from .protocol import (
    APPROVAL_CHOICES,
    AgentLoopBackend,
    AgentLoopError,
    AgentLoopEvent,
    AgentLoopRequest,
)

logger = logging.getLogger(__name__)

#: Reap a child after this much idle time (no turn touched it). Lazy: the
#: check runs whenever a handle is acquired, so no background task, no
#: shutdown hook.
REAP_AFTER_SECONDS = 600.0

#: Upper bound on live ACP children per manager. Each child is a full agent
#: runtime (hundreds of MB); without a cap the count tracks the number of
#: distinct KAGWeb sessions and is an exhaustion vector. Sessions beyond the
#: cap fail with a clear error instead of silently over-subscribing the host.
MAX_ACTIVE_CHILDREN = 8

#: Bounded budget for the settings-page handshake probe.
_PROBE_TIMEOUT_SECONDS = 10.0

#: ACP protocol version the SDK negotiates; ``initialize`` must offer one.
try:  # pragma: no cover - trivial constant passthrough
    from acp import PROTOCOL_VERSION as _ACP_PROTOCOL_VERSION
except ImportError:  # pragma: no cover - the extra is not installed
    _ACP_PROTOCOL_VERSION = 1


# ---------------------------------------------------------------------------
# Agent-side handler: translates inbound ACP traffic into neutral events
# ---------------------------------------------------------------------------


class _AcpClientHandler:  # pragma: no cover - exercised through the backend
    """The ``acp.Client`` half of the connection.

    Instantiated per session handle. ``session_update`` and
    ``request_permission`` are invoked from the SDK's reader task; events go
    to ``sink`` — a queue the active turn's generator drains. Message deltas
    buffer here and flush as whole content blocks at tool/plan boundaries
    and at turn end (content events are complete blocks, not deltas).
    """

    def __init__(self) -> None:
        self.sink: asyncio.Queue[AgentLoopEvent | None] = asyncio.Queue()
        self._pending: dict[str, asyncio.Future[str]] = {}
        #: What each parked request is waiting for, so the cancel sweep can
        #: answer them in their own vocabulary: an approval denies, a clarify
        #: is declined. Resolving a clarify with the approval word "deny"
        #: would hand the agent a literal "deny" as the user's *answer*.
        self._pending_kind: dict[str, str] = {}
        self._text_buf: list[str] = []
        self._counter = 0

    def _emit(self, event: AgentLoopEvent) -> None:
        self.sink.put_nowait(event)

    def flush_text_buffer(self) -> None:
        """Emit buffered message deltas as one content block, if any."""
        if self._text_buf:
            text = "".join(self._text_buf)
            self._text_buf.clear()
            if text.strip():
                self._emit(AgentLoopEvent("content", text=text))

    # -- inbound notifications -------------------------------------------------

    async def session_update(self, session_id: str, update: Any, **kwargs: Any) -> None:
        self._translate_update(update)

    def _translate_update(self, update: Any) -> None:
        import acp.schema as schema

        if isinstance(update, schema.AgentMessageChunk):
            text = _block_text(update.content)
            if text:
                self._text_buf.append(text)
            return
        if isinstance(update, schema.AgentThoughtChunk):
            text = _block_text(update.content)
            if text:
                self._emit(AgentLoopEvent("thinking", text=text))
            return
        # A tool call or plan interrupts the current message: flush first so
        # the block boundary lands where the agent's narration ended.
        if isinstance(
            update,
            (
                schema.ToolCallStart,
                schema.ToolCallUpdate,
                schema.ToolCallProgress,
                schema.AgentPlanUpdate,
            ),
        ):
            self.flush_text_buffer()
        if isinstance(update, schema.ToolCallStart):
            self._emit(
                AgentLoopEvent(
                    "tool_call",
                    name=str(update.title or update.kind or "tool"),
                    text=_tool_call_preview(update),
                    data={"id": str(update.tool_call_id or "")},
                )
            )
            return
        if isinstance(update, (schema.ToolCallUpdate, schema.ToolCallProgress)):
            status = str(getattr(update, "status", "") or "")
            if status not in {"completed", "failed"}:
                return  # pending / in_progress — the start event already shows
            self._emit(
                AgentLoopEvent(
                    "tool_result",
                    name=str(update.title or update.kind or "tool"),
                    text=_block_text(
                        getattr(update, "content", None) or getattr(update, "raw_output", None)
                    ),
                    data={
                        "id": str(update.tool_call_id or ""),
                        "is_error": status == "failed",
                    },
                )
            )
            return
        if isinstance(update, schema.AgentPlanUpdate):
            # PlanEntry carries {content, priority, status} — show the board.
            marks = {"completed": "x", "in_progress": "→"}
            lines = []
            for entry in update.entries or []:
                mark = marks.get(str(getattr(entry, "status", "")), "•")
                content = str(getattr(entry, "content", "") or "").strip()
                if content:
                    lines.append(f"[{mark}] {content}")
            if lines:
                self._emit(AgentLoopEvent("progress", text="\n".join(lines)))
            return
        if isinstance(update, schema.UsageUpdate):
            return  # the prompt response carries usage; no duplicate progress
        # UserMessageChunk and vendor extensions are not for the user.

    # -- inbound requests ------------------------------------------------------

    async def request_permission(
        self, session_id: str, tool_call: Any, options: list[Any], **kwargs: Any
    ) -> Any:

        self._counter += 1
        request_id = f"acp-approval-{self._counter}"
        loop = asyncio.get_running_loop()
        future: asyncio.Future[str] = loop.create_future()
        self._pending[request_id] = future
        self._pending_kind[request_id] = "approval"
        self._emit(
            AgentLoopEvent(
                "approval_request",
                # ``name`` names the action in the "run X?" sentence and must
                # stay short; ``text`` is the detail shown underneath it.
                name=_tool_call_label(tool_call),
                text=_tool_call_preview(tool_call),
                data={"request_id": request_id, "choices": list(APPROVAL_CHOICES)},
            )
        )
        choice = await future
        return _permission_response(choice, session_id, tool_call, options)

    async def create_elicitation(self, message: str, mode: Any, **kwargs: Any) -> Any:
        """Ask the user a question mid-turn, via ACP's elicitation surface.

        Intellect delivers its ``clarify`` tool this way rather than through
        ``request_permission``: a permission is a bounded decision with a
        policy fallback, while a clarify is an open question whose answer
        feeds the next model call. Both park the turn the same way here, so
        the existing ``clarify_request`` card path carries it unchanged.

        Only the *form* modes carry something a card can render. A URL mode
        (an out-of-band link, e.g. a sign-in page) has no answer to collect,
        so it is declined rather than left hanging — a client that cannot
        show the link must not stall the agent on it.
        """
        from acp import schema

        requested_schema = getattr(mode, "requested_schema", None)
        if requested_schema is None:
            logger.debug(
                "agent-loop: declining a non-form elicitation (%s)",
                type(mode).__name__,
            )
            return schema.DeclineElicitationResponse(action="decline")

        question, choices = _elicitation_question(message, requested_schema)
        self._counter += 1
        request_id = f"acp-clarify-{self._counter}"
        loop = asyncio.get_running_loop()
        future: asyncio.Future[str] = loop.create_future()
        self._pending[request_id] = future
        self._pending_kind[request_id] = "clarify"
        self._emit(
            AgentLoopEvent(
                "clarify_request",
                text=question,
                data={"request_id": request_id, "choices": choices},
            )
        )
        answer = await future
        if not str(answer or "").strip():
            # No answer (skipped, timed out, or cancelled with the turn):
            # ``decline`` is how the agent learns not to act on one.
            return schema.DeclineElicitationResponse(action="decline")
        # Intellect reads ``content["answer"]`` and accepts either a string or
        # a one-element list for a single-select property.
        return schema.AcceptElicitationResponse(action="accept", content={"answer": answer})

    def resolve_pending(self, request_id: str, choice: str) -> bool:
        """Resolve one parked request; ``False`` when unknown."""
        future = self._pending.pop(request_id, None)
        self._pending_kind.pop(request_id, None)
        if future is None or future.done():
            return False
        future.set_result(choice)
        return True

    def deny_all_pending(self) -> None:
        """Sweep every parked request when the turn goes away.

        Each kind is answered in its own vocabulary: an approval denies, a
        clarify is left unanswered (empty), which the elicitation handler
        turns into a decline.
        """
        for request_id, kind in list(self._pending_kind.items()):
            self.resolve_pending(request_id, "deny" if kind == "approval" else "")


def _tool_call_preview(tool_call: Any) -> str:
    """The full human detail for a tool call, for the approval card body.

    One source is chosen rather than concatenated: ACP agents send the same
    content twice in different renderings (Intellect's ``title`` is
    ``"<report>: <command>"`` and its ``content`` is
    ``"<report>\\n$ <command>"``), so joining them would print the whole
    report twice. ``content`` is the richest form, ``title`` is the
    fallback, and the concrete input is the last resort — it matters that
    the body is never empty, because the label above it is clamped and the
    user is approving something they must be able to read.

    ``content`` nests one level deeper than the block list itself —
    ``ContentToolCallContent`` carries the real text blocks under
    ``.content`` — so this reuses :func:`_block_text` rather than reading
    ``.text`` off the wrapper and getting an empty string.
    """
    if tool_call is None:
        return ""
    content = _block_text(getattr(tool_call, "content", None)).strip()
    if content:
        return content
    title = str(getattr(tool_call, "title", "") or "").strip()
    if title:
        return title
    return _raw_input_text(tool_call)


def _tool_call_label(tool_call: Any, *, limit: int = 80) -> str:
    """A short, single-line label naming the action awaiting approval.

    The card reads ``The agent wants to run "<label>". Allow it to
    continue?``, so the label has to be a name, not a transcript. ACP's
    ``title`` is meant to be the human-readable title, but agents put whole
    command descriptions there — Intellect sends ``"<scan report>:
    <command>"``, thousands of characters of multi-line text — so the
    concrete input wins: its first line is the command the user is actually
    being asked to allow. ``title`` is next and ``kind`` last, since a
    descriptive title beats the protocol's terse category (``read``,
    ``execute``) when there is no input to name.

    The clamp is display-only; :func:`_tool_call_preview` still carries the
    untruncated text.
    """
    for candidate in (
        _raw_input_text(tool_call),
        str(getattr(tool_call, "title", "") or ""),
        str(getattr(tool_call, "kind", "") or ""),
    ):
        label = first_line_label(candidate, limit=limit)
        if label:
            return label
    return "tool"


def _raw_input_text(tool_call: Any) -> str:
    """The concrete argument ACP agents send alongside a tool call.

    A short allow-list of keys, most-actionable first: whatever names the
    thing being run (the command, the file, the query). Anything else is
    agent-shaped payload this card has no business interpreting.
    """
    raw_input = getattr(tool_call, "raw_input", None)
    if not isinstance(raw_input, dict):
        return ""
    for key in ("command", "file_path", "path", "query", "pattern", "url"):
        value = raw_input.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _auth_method_names(auth_methods: Any) -> str:
    """Human labels for the advertised auth methods, in order."""
    return ", ".join(
        str(getattr(method, "name", "") or getattr(method, "id", "") or "?")
        for method in (auth_methods or [])
    )


#: The ACP auth-method kind that means "run an interactive setup yourself".
#: Every other kind (``agent``, ``env_var``) is a credential the agent can
#: already use, so seeing one means the install is ready.
_TERMINAL_AUTH_TYPE = "terminal"


def _auth_needs_setup(auth_methods: Any) -> bool:
    """Whether the agent has **no** ready credential path.

    ACP agents advertise every way a client may authenticate. A *terminal*
    method is an out-of-band errand ("open the setup wizard"), not a usable
    credential; anything else is one the agent can already spend.

    This used to be checked as "any method at all", which is wrong in the
    common case: the ACP registry requires an agent to advertise at least one
    method, and Intellect always appends a terminal one — so a fully
    configured install still got told "no provider credentials; run login",
    sending the operator to redo a login that already worked.

    An empty list is not a problem either: an agent that publishes no auth
    methods is simply not asking for any.
    """
    methods = list(auth_methods or [])
    if not methods:
        return False
    return all(str(getattr(method, "type", "") or "") == _TERMINAL_AUTH_TYPE for method in methods)


def _incomplete_stop_reason(stop_reason: str) -> str:
    """A learner-facing note when the agent stopped short, else ``""``.

    ACP names why a prompt ended: ``end_turn`` is the only completion. The
    others (``max_tokens``, ``refusal``, ``max_turn_requests``, or a vendor
    value this build has never seen) all mean the agent was cut off, and the
    streamed text is a prefix rather than an answer. Treating those as
    success is how a truncated reply reaches the reader labelled "completed".
    """
    reason = str(stop_reason or "").strip()
    if not reason or reason == "end_turn":
        return ""
    explored = {
        "max_tokens": "output length limit reached",
        "max_turn_requests": "turn request limit reached",
        "refusal": "the agent refused to continue",
    }
    detail = explored.get(reason, f"stopped early: {reason}")
    return t("agent_loop.acp_turn_incomplete", reason=detail)


def _elicitation_question(message: str, requested_schema: Any) -> tuple[str, list[str]]:
    """``(question, choices)`` for a form-mode elicitation.

    ACP describes the form as JSON-Schema-ish properties. Intellect asks a
    single free-or-enumerated question under the key ``answer``
    (``acp_adapter/clarify.py``), so that is what is read; a schema with
    several properties cannot be shown faithfully on a one-question card, and
    the question text is then the best available summary. Anything the agent
    labels the field with is preferred over the raw key, and the ``message``
    is the fallback so the card is never blank.
    """
    properties = getattr(requested_schema, "properties", None)
    question = str(message or "").strip()
    choices: list[str] = []
    if isinstance(properties, dict):
        # Intellect's key wins when present, else the first property — a
        # single-question form has exactly one either way.
        field = properties.get("answer")
        if field is None and properties:
            field = next(iter(properties.values()))
        if field is not None:
            title = str(getattr(field, "title", "") or "").strip()
            if title:
                question = title
            raw_enum = getattr(field, "enum", None)
            if isinstance(raw_enum, (list, tuple)):
                choices = [str(choice) for choice in raw_enum if str(choice).strip()]
    return question, choices


def _permission_response(
    choice: str, session_id: str, tool_call: Any, options: list[Any]
) -> dict[str, Any]:
    """Map our decision vocabulary onto the agent's permission options.

    Returns the wire-shaped dict directly: the SDK serializes handler
    responses shallowly, so nested response models do not survive.
    """
    wanted = {
        "once": "allow_once",
        "session": "allow_always",
        "always": "allow_always",
        "deny": "reject_once",
    }.get(choice, "reject_once")
    options = list(options or [])
    picked = next((option for option in options if getattr(option, "kind", "") == wanted), None)
    if picked is None and choice != "deny":
        # No option of the preferred kind: fall back to the first allow-ish
        # option; if the agent offered none, the turn is denied outright.
        picked = next(
            (option for option in options if str(getattr(option, "kind", "")).startswith("allow")),
            None,
        )
    if choice == "deny" or picked is None:
        # DeniedOutcome: no option id, the agent cancels the pending action.
        return {"outcome": {"outcome": "cancelled"}}
    return {
        "outcome": {
            "outcome": "selected",
            "optionId": str(getattr(picked, "option_id", "")),
        }
    }


def _block_text(content: Any) -> str:
    """Flatten ACP content blocks to text.

    ``content`` is usually a block list, but the SDK helpers (and some
    agents) also produce a single block — iterating that yields field
    tuples, so normalize first.
    """
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    blocks = content if isinstance(content, (list, tuple)) else [content]
    texts: list[str] = []
    for block in blocks:
        text = str(getattr(block, "text", "") or "")
        if not text:
            # ToolCallContent wraps the payload: ContentToolCallContent
            # carries the actual blocks under ``.content``.
            text = _block_text(getattr(block, "content", None))
        if text:
            texts.append(text)
    return "".join(texts)


# ---------------------------------------------------------------------------
# Session manager: one child per KAGWeb session, lazy idle reaping
# ---------------------------------------------------------------------------


@dataclass
class AcpSessionHandle:
    """One live child and its attached agent session."""

    key: str
    client: Any = None
    connection: Any = None
    process: Any = None
    transport_cm: Any = None
    acp_session_id: str = ""
    cwd: str = ""
    init_response: Any = None
    last_used: float = field(default_factory=time.monotonic)

    def touch(self) -> None:
        self.last_used = time.monotonic()

    @property
    def alive(self) -> bool:
        return self.process is not None and self.process.returncode is None


class AcpSessionManager:
    """Owns the per-session ACP children for one backend configuration."""

    def __init__(self, config_key: str, *, max_children: int = MAX_ACTIVE_CHILDREN) -> None:
        self._config_key = config_key
        self.max_children = max_children
        self._handles: dict[str, AcpSessionHandle] = {}
        self._spawn: Any = None  # async (handle, cwd) -> None; set by backend

    def set_spawner(self, spawn: Any) -> None:
        self._spawn = spawn

    async def ensure(self, session_key: str, cwd: str) -> AcpSessionHandle:
        """Return a live handle, (re)spawning the child when needed."""
        await self._reap()
        handle = self._handles.get(session_key)
        if handle is not None and handle.alive:
            handle.touch()
            return handle
        if handle is not None:
            # Crashed child: drop the transport but keep the ACP session id
            # and cwd so the respawn can re-attach via load_session.
            previous_id, previous_cwd = handle.acp_session_id, handle.cwd
            await self.discard(session_key)
            handle = AcpSessionHandle(
                key=session_key, cwd=previous_cwd or cwd, acp_session_id=previous_id
            )
        else:
            handle = AcpSessionHandle(key=session_key, cwd=cwd)
        # Did the id below come off disk? Only that source can outlive a
        # failed spawn, so only that source needs the forget-and-retry below.
        restored_from_disk = False
        if not handle.acp_session_id:
            # Nothing survives in memory across a KAGWeb restart, so the id
            # has to come back off disk or the agent starts a fresh session
            # and answers with no memory of the conversation.
            stored = load_acp_session(session_key, config_key=self._config_key)
            if stored is not None:
                stored_id, stored_cwd = stored
                # The stored cwd is an absolute path from this session's own
                # workspace. That directory can be gone (its files were
                # reclaimed, the workspace root moved) while the record
                # survives, and spawning into a missing directory raises
                # before the handshake — every turn would fail, and because
                # the record is only written after a *successful* spawn it
                # would never be replaced. Treat a vanished cwd as a stale
                # record and start over in the caller's directory.
                if not stored_cwd or Path(stored_cwd).is_dir():
                    handle.acp_session_id = stored_id
                    if stored_cwd:
                        handle.cwd = stored_cwd
                    restored_from_disk = True
                else:
                    forget_acp_session(session_key)
        if self._spawn is None:  # pragma: no cover - backend wires this first
            raise AgentLoopError("ACP session manager is not wired", backend="acp")
        live = sum(1 for item in self._handles.values() if item.alive)
        if live >= self.max_children:
            # Fail fast with an actionable message instead of silently
            # over-subscribing the host with agent runtimes.
            raise AgentLoopError(
                t("agent_loop.acp_too_many_sessions", max=self.max_children),
                backend="acp",
            )
        try:
            await self._spawn(handle)
        except Exception:
            if not restored_from_disk:
                raise
            # The agent rejected what we restored (an id it no longer holds,
            # a cwd it cannot use). Drop the record and start clean once, so
            # one unusable record cannot wedge this session for good.
            forget_acp_session(session_key)
            handle = AcpSessionHandle(key=session_key, cwd=cwd)
            await self._spawn(handle)
        self._handles[session_key] = handle
        handle.touch()
        return handle

    async def discard(self, session_key: str) -> None:
        handle = self._handles.pop(session_key, None)
        if handle is not None:
            await _close_handle(handle)

    async def _reap(self) -> None:
        stale = [
            key
            for key, handle in self._handles.items()
            if time.monotonic() - handle.last_used > REAP_AFTER_SECONDS
        ]
        for key in stale:
            await self.discard(key)

    async def close_all(self) -> None:
        for key in list(self._handles):
            await self.discard(key)


async def _close_handle(handle: AcpSessionHandle) -> None:
    if hasattr(handle.client, "deny_all_pending"):
        handle.client.deny_all_pending()
    connection = getattr(handle, "connection", None)
    if connection is not None:
        try:
            await connection.close()
        except Exception:  # noqa: BLE001 - closing must never raise
            pass
    process = getattr(handle, "process", None)
    if process is not None and process.returncode is None:
        try:
            process.terminate()
            await asyncio.wait_for(process.wait(), timeout=5)
        except Exception:  # noqa: BLE001
            try:
                process.kill()
            except Exception:  # noqa: BLE001
                pass
    cm = getattr(handle, "transport_cm", None)
    if cm is not None:
        try:
            await cm.__aexit__(None, None, None)
        except Exception:  # noqa: BLE001
            pass


_MANAGERS: dict[str, AcpSessionManager] = {}


def get_acp_session_manager(config_key: str) -> AcpSessionManager:
    """One manager per backend configuration (command + env shape)."""
    manager = _MANAGERS.get(config_key)
    if manager is None:
        manager = AcpSessionManager(config_key)
        _MANAGERS[config_key] = manager
    return manager


async def shutdown_all_acp_sessions() -> None:
    """Gracefully close every tracked ACP child. Wired into the API app's
    lifespan shutdown — without this, one child per recently-active session
    survives the server as an orphan (the lazy reaper never runs again)."""
    for manager in list(_MANAGERS.values()):
        await manager.close_all()


def _terminate_tracked_children_sync() -> None:  # pragma: no cover - exit path
    """Last-resort sweep at interpreter exit.

    The event loop may already be gone, so no awaits: signal live children
    directly. Best effort — a child that ignores SIGTERM is left to the OS
    reaper rather than blocking interpreter shutdown.
    """
    for manager in _MANAGERS.values():
        for handle in list(manager._handles.values()):
            process = getattr(handle, "process", None)
            if process is not None and process.returncode is None:
                try:
                    process.terminate()
                except Exception:
                    pass


try:
    import atexit

    atexit.register(_terminate_tracked_children_sync)
except Exception:  # pragma: no cover - embedded interpreters without atexit
    pass


# ---------------------------------------------------------------------------
# The backend
# ---------------------------------------------------------------------------


class AcpAgentLoopBackend(AgentLoopBackend):
    """Drive one ACP agent child; stream its work as neutral events."""

    uses_workdir = True
    supports_control = True

    def __init__(
        self,
        *,
        name: str,
        command: str,
        base_args: list[str],
        env: dict[str, str],
        timeout_seconds: float,
        model: str = "",
    ) -> None:
        self.name = name
        self.command = command
        self.base_args = list(base_args)
        self.env = {str(key): str(value) for key, value in (env or {}).items()}
        self.timeout_seconds = float(timeout_seconds) if timeout_seconds else 0.0
        #: The profile's chosen model. The ACP surface has no argv placeholder
        #: to substitute it into (the handshake negotiates the agent's session),
        #: so it is recorded for diagnostics; a future ACP model-select request
        #: would read it here. Per-turn overrides (``AgentLoopRequest.model``)
        #: have no ACP transport either — ``per_turn_model`` presets never
        #: include this family, so the picker stays hidden for ACP backends.
        self.model = str(model or "").strip()
        # Hash the config into the manager key: the operator env block may
        # carry credentials, and they must not sit in plaintext dict keys.
        import hashlib
        import json as _json

        config_key = hashlib.sha256(
            _json.dumps([command, sorted(self.env.items())]).encode()
        ).hexdigest()[:16]
        #: Identifies this backend configuration; stored with each session
        #: record so an id minted by one agent install is never reused
        #: against a different one.
        self._config_key = config_key
        self._manager = get_acp_session_manager(config_key)
        self._manager.set_spawner(self._spawn_session)
        self._current_key = ""

    # -- session lifecycle -----------------------------------------------------

    async def _spawn_session(self, handle: AcpSessionHandle) -> None:
        """Spawn the child, run the ACP handshake, attach the agent session."""
        try:
            import acp
            import acp.schema as schema
        except ImportError as exc:  # pragma: no cover - guarded by the factory
            raise AgentLoopError(
                t("agent_loop.acp_sdk_missing", backend=self.name), backend=self.name
            ) from exc

        import os

        from .cli_backend import _build_child_env

        env = _build_child_env(dict(os.environ), self.env)
        cm = acp.spawn_stdio_transport(
            self.command,
            *self.base_args,
            env=env or None,
            cwd=handle.cwd or None,
            stderr=None,  # agent logs go to stderr; inherit so pipes never fill
        )
        reader, writer, process = await cm.__aenter__()
        handle.transport_cm = cm
        handle.process = process
        handle.client = _AcpClientHandler()
        try:
            connection = acp.connect_to_agent(
                handle.client,
                writer,
                reader,
                # Intellect delivers its ``clarify`` tool through ACP
                # elicitation, which the SDK still gates behind this flag: the
                # route is registered but rejects calls as "method not found"
                # until it is set, whatever the client implements. Declaring it
                # here is what makes a question reach the user instead of
                # failing the turn with a protocol error.
                use_unstable_protocol=True,
            )
            handle.init_response = await connection.initialize(
                _ACP_PROTOCOL_VERSION,
                # Answer the agent's capability question truthfully: we can
                # present a form question, and we do not serve its file-system
                # or terminal requests (the agent runs its own tooling).
                client_capabilities=schema.ClientCapabilities(
                    elicitation=schema.ElicitationCapabilities(
                        form=schema.ElicitationFormCapabilities(),
                    ),
                ),
                client_info=schema.Implementation(name="kagweb", title="KAGWeb", version=""),
            )
            if handle.acp_session_id:
                # Child died earlier, or this is a fresh process after a
                # KAGWeb restart: re-attach the recorded agent session so
                # history and compression chains survive.
                try:
                    await connection.load_session(
                        cwd=handle.cwd or "", session_id=handle.acp_session_id
                    )
                except Exception:  # noqa: BLE001 - stale id: start a fresh one
                    response = await connection.new_session(cwd=handle.cwd or "")
                    handle.acp_session_id = str(response.session_id)
            else:
                response = await connection.new_session(cwd=handle.cwd or "")
                handle.acp_session_id = str(response.session_id)
            # Record it so the next process can re-attach too. Probe children
            # are throwaway readiness checks whose key dies with the request,
            # so recording them would only litter the store.
            if not handle.key.startswith("probe-"):
                save_acp_session(
                    handle.key,
                    config_key=self._config_key,
                    session_id=handle.acp_session_id,
                    cwd=handle.cwd or "",
                )
        except AgentLoopError:
            await _close_handle(handle)
            raise
        except Exception as exc:
            await _close_handle(handle)
            raise AgentLoopError(
                t("agent_loop.acp_init_failed", backend=self.name, error=str(exc)),
                backend=self.name,
            ) from exc
        handle.connection = connection

    # -- one turn ---------------------------------------------------------------

    async def run(self, request: AgentLoopRequest) -> AsyncIterator[AgentLoopEvent]:
        import acp.schema as schema

        session_key = request.session_id or "default"
        self._current_key = session_key
        handle = await self._manager.ensure(session_key, request.workdir)
        q: asyncio.Queue[AgentLoopEvent | None] = asyncio.Queue()
        handle.client.sink = q
        prompt_task = asyncio.create_task(
            handle.connection.prompt(
                handle.acp_session_id,
                [schema.TextContentBlock(type="text", text=request.prompt)],
            )
        )
        stop_reason = ""
        usage: dict[str, Any] = {}
        error_detail = ""
        try:
            while True:
                get_task = asyncio.create_task(q.get())
                done, _ = await asyncio.wait(
                    {get_task, prompt_task}, return_when=asyncio.FIRST_COMPLETED
                )
                if get_task in done:
                    item = get_task.result()
                    if item is not None:
                        yield item
                else:
                    get_task.cancel()
                if prompt_task in done:
                    # The response arrives after the agent's final updates on
                    # the same connection, but flush the tail of the buffer
                    # and the queue anyway so nothing streams past the end.
                    handle.client.flush_text_buffer()
                    while not q.empty():
                        item = q.get_nowait()
                        if item is not None:
                            yield item
                    try:
                        stop = prompt_task.result()
                        stop_reason = str(getattr(stop, "stop_reason", "") or "end_turn")
                        raw_usage = getattr(stop, "usage", None)
                        if raw_usage is not None:
                            for name in (
                                "input_tokens",
                                "output_tokens",
                                "thought_tokens",
                                "total_tokens",
                            ):
                                value = getattr(raw_usage, name, None)
                                if value is not None:
                                    usage[name] = value
                    except Exception as exc:  # noqa: BLE001 - child died mid-turn
                        error_detail = str(exc)
                    break
        finally:
            if not prompt_task.done():
                # Ask the agent to stop first; a hard cancel of the pending
                # request would poison the connection for the next turn.
                _fire_and_forget(handle.connection.cancel(handle.acp_session_id))
                try:
                    await asyncio.wait_for(asyncio.shield(prompt_task), timeout=5)
                except Exception:  # noqa: BLE001 - cancellation is best effort
                    prompt_task.cancel()
            # A cancelled turn must not leave the agent waiting on a permission
            # decision nobody will send.
            handle.client.deny_all_pending()
            handle.client.flush_text_buffer()
            handle.client.sink = asyncio.Queue()  # late updates land nowhere
            if stop_reason == "cancelled":
                _fire_and_forget(handle.connection.cancel(handle.acp_session_id))
            handle.touch()

        if usage:
            yield AgentLoopEvent("usage", data=usage)
        if error_detail:
            raise AgentLoopError(
                t("agent_loop.acp_turn_failed", backend=self.name, error=error_detail),
                backend=self.name,
            )
        if stop_reason == "cancelled":
            # The agent stopped on request; whatever streamed already did.
            return
        # Anything but ``end_turn`` means the agent gave up early — it hit the
        # output ceiling, refused, or ran out of turns. The wire signal is
        # easy to lose (an adapter that maps every finish to ``end_turn``
        # looks identical to a clean answer), so surface it rather than let a
        # half-finished reply read as complete.
        incomplete = _incomplete_stop_reason(stop_reason)
        if incomplete:
            yield AgentLoopEvent("error", text=incomplete, data={"stop_reason": stop_reason})

    # -- control plane ----------------------------------------------------------

    async def probe(self) -> tuple[bool, str]:
        """Definitive readiness check: spawn, ACP handshake, attach, shut down.

        No turn is sent. The throwaway handle is discarded immediately; a
        missing provider credential surfaces through the agent's advertised
        auth methods.
        """
        key = f"probe-{time.monotonic_ns()}"
        try:
            # Bounded: a child stuck in startup (first-run onboarding, slow
            # disk) must fail the probe, not pin the admin request forever.
            handle = await asyncio.wait_for(
                self._manager.ensure(key, ""), timeout=_PROBE_TIMEOUT_SECONDS
            )
        except asyncio.TimeoutError:
            await self._manager.discard(key)
            return (
                False,
                t(
                    "agent_loop.acp_probe_timeout",
                    backend=self.name,
                    seconds=int(_PROBE_TIMEOUT_SECONDS),
                ),
            )
        except AgentLoopError as exc:
            return False, str(exc)
        except Exception as exc:  # noqa: BLE001 - probe reports everything
            await self._manager.discard(key)
            return False, t("agent_loop.acp_init_failed", backend=self.name, error=str(exc))
        auth_methods = getattr(getattr(handle, "init_response", None), "auth_methods", None)
        detail = f"handshake ok; agent session attached ({handle.acp_session_id})."
        if _auth_needs_setup(auth_methods):
            detail += (
                " The agent offers no ready credentials — only an interactive "
                "setup method. Run `intellect` login first "
                f"(methods: {_auth_method_names(auth_methods)})."
            )
        await self._manager.discard(key)
        return True, detail

    async def respond_approval(self, request_id: str, choice: str) -> None:
        handle = self._manager._handles.get(self._current_key)
        if handle is None or not handle.alive:
            return
        handle.client.resolve_pending(request_id, choice)

    async def respond_clarify(self, request_id: str, answer: str) -> None:
        """Deliver the user's answer to a question the agent asked mid-turn.

        Same parked-future shape as an approval: the handler is still awaiting
        inside ``create_elicitation``, and resolving it is what turns the
        answer into the elicitation response the agent reads.
        """
        handle = self._manager._handles.get(self._current_key)
        if handle is None or not handle.alive:
            return
        handle.client.resolve_pending(request_id, answer)

    async def cancel(self) -> None:
        handle = self._manager._handles.get(self._current_key)
        if handle is not None and handle.alive and handle.acp_session_id:
            await handle.connection.cancel(handle.acp_session_id)


def _fire_and_forget(coro: Any) -> None:
    async def _run() -> None:
        try:
            await coro
        except Exception:  # noqa: BLE001 - cancel is best effort
            pass

    asyncio.ensure_future(_run())
