"""HTTP-family agent-loop backend: one streaming POST per turn.

For agent services reached over the network — Intellect community /
enterprise, Hermes, AgentScope, or any custom service. The wire contract
is intentionally small so an adapter shim on the service side is a few
lines:

``POST {url}{turn_path}`` with JSON body::

    {"session_id": "…", "language": "en",
     "prompt": "user turn text",
     "history": [{"role": "user"|"assistant", "content": "…"}, …]}

Headers: ``Authorization: Bearer <api_key>`` when an API key is set, plus
any operator-configured extras.

The response streams progress as **SSE** (``text/event-stream``; each
``data:`` line is one JSON object) or **NDJSON** (any other content type;
one JSON object per line). Each object is either already neutral —
``{"kind": "content", "text": "…"}`` with kinds from
:data:`kagweb.services.agent_loop.protocol.EVENT_KINDS` — or a generic
shape the heuristic translator maps. The stream ending normally completes
the turn; HTTP errors and protocol violations raise and fail it.

Unlike the CLI family, the loop's code execution happens inside the
operator's service, not the KAGWeb process — this is the family to use
for multi-user deployments.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import nullcontext
import json
import logging
from typing import Any
from urllib.parse import quote

import httpx

from kagweb.services.i18n import t

from .cli_backend import translate_generic
from .identity import resolve_backend_identity
from .protocol import (
    APPROVAL_CHOICES,
    EVENT_KINDS,
    MAX_LINE_BYTES,
    AgentLoopBackend,
    AgentLoopError,
    AgentLoopEvent,
    AgentLoopRequest,
)


def _path_segment(value: str) -> str:
    """Percent-encode a value for use as ONE path segment.

    The values interpolated into these URLs (`run_id`, `session_id`) come from
    the remote backend's responses and event stream, and this client attaches
    the operator's ``Authorization`` header to every request it builds. Left
    raw, a value like ``../../admin`` is normalised by httpx into a different
    path on the configured host, and ``?``/``#`` split off a query or fragment —
    so a compromised or misconfigured backend could aim the operator's
    credentials at an endpoint that was never intended. Encoding keeps the
    value inside its own segment.

    Typed `str` on purpose: a caller that might hold `None` must decide what an
    absent id means before building a URL, rather than have it silently become
    the literal "None" in the path.
    """
    return quote(str(value), safe="")


#: The instance-tenant header, under both names the Intellect api_server
#: implementations read (Rust / Python). See ``_tenant_headers``.
_HEADER_TENANT_RUST = "X-Tenant-Id"
_HEADER_TENANT_PYTHON = "X-Intellect-Tenant-Id"


def _pretty_arguments(raw: Any) -> str:
    """Render tool arguments for the approval card body.

    The run channel hands over an already-redacted JSON *string*, so printing
    it verbatim puts a whole argument set on one long line. Pretty-printing
    makes it readable; anything that is not a JSON object/array is passed
    through unchanged — the value is still disclosed, just not reshaped.
    """
    text = str(raw or "")
    if not text.strip():
        return ""
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, TypeError, ValueError):
        return text
    if isinstance(parsed, (dict, list)):
        return json.dumps(parsed, indent=2, ensure_ascii=False, sort_keys=True)
    return text


def _namespaced_session(prefix: str, session_id: str) -> str:
    """Scope a session id to the account it belongs to, when a prefix is set.

    The service keys its sessions by the id it is handed, and every run's
    access check consults that id. Without a namespace, two KAGWeb accounts
    resolve to the same remote session whenever their ids coincide — which ids
    from a restored or imported store can. Empty prefix leaves the id untouched
    so a deployment that never opted into identity sends exactly what it did
    before.
    """
    session_id = str(session_id or "")
    prefix = str(prefix or "")
    if not session_id or not prefix:
        return session_id
    return f"{prefix}{session_id}"


logger = logging.getLogger(__name__)

_ERROR_BODY_LIMIT = 2000

#: Degraded-poll ceiling: 150 rounds x 2s = 5 minutes of status polling
#: after the event stream is lost, then the turn surfaces an error.
_POLL_MAX_ITERATIONS = 150


def _neutral_event(obj: dict[str, Any], state: dict[str, Any]) -> AgentLoopEvent | None:
    """Map one response object to a neutral event, tolerating vendor shapes."""
    kind = str(obj.get("kind") or "").strip().lower()
    if kind in EVENT_KINDS:
        data = obj.get("data") if isinstance(obj.get("data"), dict) else {}
        return AgentLoopEvent(
            kind,
            text=str(obj.get("text") or ""),
            name=str(obj.get("name") or ""),
            data=data,
        )
    events = translate_generic(obj, state)
    return events[0] if events else None


#: Ledger keys kept on the per-turn ``state`` dict so the end of a stream can
#: tell "the backend said nothing" from "the backend said things we could not
#: read". The second is a configuration error worth failing on; the first is
#: an empty answer, which the capability layer already reports.
_FRAMES_SEEN = "frames_seen"
_UNMAPPED_FRAMES = "unmapped_frames"


def _short_finish_reason(obj: dict[str, Any]) -> str:
    """Human reason a run stopped short, or ``""`` for a clean finish.

    The runs channel reports ``completed`` / ``partial`` / ``error`` on
    ``run.completed``. A run that is not complete is a *prefix* — the agent hit
    its output ceiling — and saying so is the whole point: without it the text
    above reads as the finished answer. Older services omit the fields
    entirely, which is treated as a clean finish.
    """
    if bool(obj.get("completed", True)):
        return ""
    error = str(obj.get("error") or "").strip()
    if error:
        return error
    return "the agent stopped before finishing"


def _stop_reason_code(obj: dict[str, Any]) -> str:
    """Machine-readable code for a short finish, matching the ACP vocabulary.

    The UI keys its "stopped short" marker on this, so the two transports
    should not invent separate vocabularies — ``max_tokens`` is what the ACP
    family reports for the same condition.
    """
    error = str(obj.get("error") or "").lower()
    if "truncat" in error or bool(obj.get("partial")):
        return "max_tokens"
    return "refusal"


def _is_openai_chat_payload(obj: dict[str, Any]) -> bool:
    """Whether ``obj`` is an OpenAI chat-completions payload, streamed or not.

    Pointing an agent-loop profile at such an endpoint looks healthy from the
    outside — HTTP 200 and a well-formed stream — while carrying none of what
    an agent turn needs. There is no reasoning channel, tool arguments and
    results are reduced to start/end markers, and nothing can answer an
    approval, a clarification or a stop. The neutral schema maps none of it,
    so the turn would otherwise finish empty with nothing to explain why.

    Matched on the payload's own shape (the ``object`` marker, or a ``choices``
    entry carrying ``delta``/``message``) rather than on an error string, so a
    vendor that omits the marker is still caught.
    """
    if str(obj.get("object") or "").startswith("chat.completion"):
        return True
    choices = obj.get("choices")
    if not isinstance(choices, list) or not choices:
        return False
    first = choices[0]
    return isinstance(first, dict) and ("delta" in first or "message" in first)


class HttpAgentLoopBackend(AgentLoopBackend):
    """POST each turn to the configured agent service and stream events back."""

    def __init__(
        self,
        *,
        name: str,
        url: str,
        turn_path: str,
        api_key: str,
        headers: dict[str, str],
        timeout_seconds: float,
        transport: httpx.AsyncBaseTransport | None = None,
        model: str = "",
        identity_mode: str = "off",
        tenant_id: str = "",
    ) -> None:
        self.name = name
        self.url = url.rstrip("/")
        self.turn_path = turn_path if turn_path.startswith("/") else f"/{turn_path}"
        self.api_key = str(api_key or "")
        self.headers = {str(k): str(v) for k, v in (headers or {}).items()}
        self.timeout_seconds = float(timeout_seconds) if timeout_seconds else 0.0
        #: The instance tenant this service runs as, when the deployment names
        #: one. Applied to every request the backend makes, from whichever
        #: method builds headers — deliberately *not* part of ``self.headers``,
        #: which :meth:`with_identity` replaces wholesale on the turn path.
        self.tenant_id = str(tenant_id or "").strip()
        #: Base key/headers, kept so applying a turn's identity is idempotent:
        #: the resolved values always derive from the profile, never from a
        #: previous turn's identity.
        self._base_api_key = self.api_key
        self._base_headers = dict(self.headers)
        #: How a turn is attributed to / delegated to the service. Applied per
        #: turn by :meth:`with_identity`; ``off`` leaves the profile's own
        #: credential in place.
        self.identity_mode = str(identity_mode or "off")
        #: Prepended to the KAGWeb session id before it reaches the service, so
        #: two accounts can never collide on one remote session. Only set when a
        #: per-account identity is in play; without it the id is sent verbatim.
        self.session_prefix = ""
        #: Set by :meth:`with_identity`. Guards the fallback below: an identity
        #: the capability applied for this turn must not be re-resolved and
        #: overwritten when ``run`` starts.
        self._identity_applied = False
        #: The profile's chosen model, sent in the request body when set. Empty
        #: means the service picks its own, and the key is omitted entirely so a
        #: deployment that never sets a model sees the exact request it did
        #: before this field existed.
        self.model = str(model or "").strip()
        # Test/embedding hook: injected httpx transport (never set by the
        # settings-driven factory).
        self._transport = transport

    def with_identity(self, identity: Any) -> None:
        """Adopt the per-turn identity resolved for the calling account.

        The factory builds a backend from the profile, which is deployment
        configuration; who the turn *runs as* is per-user and only known in the
        request, so it arrives here instead. Applying it means replacing the
        credential wholesale rather than merging: ``api_key`` may be the user's
        own token in place of the service key, and a profile-configured header
        of the same name must not survive next to the resolved one.

        Always derived from the profile's base values, so calling this twice —
        or once per turn on a reused instance — cannot compound.
        """
        if identity is None:
            return
        self._identity_applied = True
        self.api_key = str(getattr(identity, "api_key", "") or "")
        resolved_headers = getattr(identity, "headers", None)
        if isinstance(resolved_headers, dict):
            # Rebuilt, not updated: a header the profile sets and the identity
            # also sets must resolve to the identity's value, and dict.update
            # would let a differently-cased profile key linger.
            self.headers = {str(key): str(value) for key, value in resolved_headers.items()}
        else:
            self.headers = dict(self._base_headers)
        self.session_prefix = str(getattr(identity, "session_prefix", "") or "")

    def _apply_profile_identity(self) -> None:
        """Resolve this turn's identity from the profile, if any.

        The capability normally applies identity before calling :meth:`run`
        (it has the turn's language for the messages, and it must be able to
        turn a broken link into the turn's error). This is the fallback for a
        direct caller — a test harness, an embedding — so a backend built with
        an identity mode never silently runs as the deployment by accident.
        """
        if self.identity_mode == "off" or self._identity_applied:
            return
        identity = resolve_backend_identity(
            {
                "api_key": self._base_api_key,
                "headers": self._base_headers,
                "identity_mode": self.identity_mode,
                # The URL is part of the decision, not just the destination: a
                # linked member token is bound to the origin it was minted
                # against, so omitting it would refuse every token outright.
                "url": self.url,
            },
            family="http",
        )
        if identity is not None:
            self.with_identity(identity)

    def endpoint(self) -> str:
        return f"{self.url}{self.turn_path}"

    def _tenant_headers(self) -> dict[str, str]:
        """The tenant header(s) for this turn, or ``{}`` when none is set.

        Two names, because the two Intellect api_server implementations read
        different ones and each ignores the other's: the Rust gateway (the
        authoritative build, and the one the ``intellect-team`` preset requires)
        validates ``X-Tenant-Id``, while the legacy Python adapter reads
        ``X-Intellect-Tenant-Id``. Sending both keeps one profile working
        against either build; a service that knows neither ignores them.

        A wrong value is not quietly tolerated on the service side — it is
        compared with that instance's configured tenant and answered with 400
        (malformed) or 403 (mismatch), which is why the settings API refuses a
        malformed id at save time instead of letting every turn fail here.
        """
        if not self.tenant_id:
            return {}
        return {_HEADER_TENANT_RUST: self.tenant_id, _HEADER_TENANT_PYTHON: self.tenant_id}

    async def run(self, request: AgentLoopRequest) -> AsyncIterator[AgentLoopEvent]:
        self._apply_profile_identity()
        payload: dict[str, Any] = {
            "session_id": _namespaced_session(self.session_prefix, request.session_id),
            "language": request.language,
            "prompt": request.prompt,
            "history": request.history,
        }
        turn_model = (request.model or self.model).strip()
        if turn_model:
            # Omitted when unset, so an existing deployment's request body is
            # byte-for-byte what it was before this field existed. A per-turn
            # override (llm_selection) wins over the profile's configured
            # model; whether the service honors the key is up to the service.
            payload["model"] = turn_model
        headers = {
            "Accept": "text/event-stream, application/x-ndjson, application/jsonl",
            **self.headers,
            **self._tenant_headers(),
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        # Per-read timeout so a dead connection cannot hang a read forever;
        # the total wall-clock cap below bounds the whole turn.
        read_timeout = self.timeout_seconds or 300.0
        timeout = httpx.Timeout(connect=15.0, read=read_timeout, write=60.0, pool=15.0)
        state: dict[str, Any] = {}
        try:
            async with httpx.AsyncClient(timeout=timeout, transport=self._transport) as client:
                async with client.stream(
                    "POST", self.endpoint(), json=payload, headers=headers
                ) as response:
                    if response.status_code != 200:
                        body = (await response.aread())[:_ERROR_BODY_LIMIT]
                        raise AgentLoopError(
                            t(
                                "agent_loop.http_status",
                                backend=self.name,
                                status=response.status_code,
                                detail=body.decode("utf-8", "replace").strip()[:400],
                            ),
                            backend=self.name,
                        )
                    content_type = str(response.headers.get("content-type") or "").lower()
                    stream = (
                        self._iter_sse(response, state)
                        if "text/event-stream" in content_type
                        else self._iter_ndjson(response, state)
                    )
                    async with (
                        asyncio.timeout(self.timeout_seconds)
                        if self.timeout_seconds
                        else nullcontext()
                    ):
                        async for event in stream:
                            state["mapped_events"] = state.get("mapped_events", 0) + 1
                            yield event
                    self._assert_stream_was_understood(state)
        except TimeoutError as exc:
            raise AgentLoopError(
                t("agent_loop.timeout", backend=self.name, seconds=int(self.timeout_seconds)),
                backend=self.name,
            ) from exc
        except httpx.HTTPError as exc:
            raise AgentLoopError(
                t("agent_loop.http_failed", backend=self.name, error=str(exc)),
                backend=self.name,
            ) from exc

    async def _iter_ndjson(
        self, response: httpx.Response, state: dict[str, Any]
    ) -> AsyncIterator[AgentLoopEvent]:
        async for line in _capped_lines(response, self.name):
            text = line.strip()
            if not text:
                continue
            try:
                obj = json.loads(text)
            except json.JSONDecodeError:
                # Length and a short prefix only: this line is unparsed stream
                # content, so it may be model text rather than framing, and the
                # log file must not become a copy of the conversation.
                logger.debug(
                    "agent-loop %s: non-JSON stream line (%d chars): %.80s",
                    self.name,
                    len(text),
                    text,
                )
                continue
            if isinstance(obj, dict):
                event = self._map_frame(obj, state)
                if event is not None:
                    yield event

    async def _iter_sse(
        self, response: httpx.Response, state: dict[str, Any]
    ) -> AsyncIterator[AgentLoopEvent]:
        data_lines: list[str] = []
        event_name = ""
        async for line in _capped_lines(response, self.name):
            if line.startswith(":"):
                continue  # SSE comment / keepalive
            if not line.strip():
                # Blank line terminates the current event, if any data.
                for event in self._sse_data_events(data_lines, state, event_name):
                    yield event
                data_lines = []
                event_name = ""
                continue
            if line.startswith("data:"):
                data_lines.append(line[len("data:") :].lstrip())
            elif line.startswith("event:"):
                # Carried into the payload: some vendors put the only type
                # marker on the SSE line (``event: intellect.tool.progress``
                # with a body that names no kind), and dropping it here is why
                # those frames used to look unmappable.
                event_name = line[len("event:") :].strip()
        for event in self._sse_data_events(data_lines, state, event_name):
            yield event

    def _map_frame(self, obj: dict[str, Any], state: dict[str, Any]) -> AgentLoopEvent | None:
        """Translate one streamed frame, recording what the stream contained.

        Both counters exist so the end of the stream can be judged: a backend
        that sent frames we could not map even once is misconfigured, which is
        not the same as one that simply had nothing to say.
        """
        state[_FRAMES_SEEN] = state.get(_FRAMES_SEEN, 0) + 1
        event = _neutral_event(obj, state)
        if event is None:
            state[_UNMAPPED_FRAMES] = state.get(_UNMAPPED_FRAMES, 0) + 1
            self._reject_unmappable_frame(obj)
        return event

    def _reject_unmappable_frame(self, obj: dict[str, Any]) -> None:
        """Fail fast on a whole payload shape this backend cannot serve.

        An OpenAI chat-completions endpoint is the case worth naming outright:
        it answers 200 and streams, so the only symptom is an empty turn.
        """
        if _is_openai_chat_payload(obj):
            raise AgentLoopError(
                t("agent_loop.openai_chat_unsupported", backend=self.name),
                backend=self.name,
            )

    def _assert_stream_was_understood(self, state: dict[str, Any]) -> None:
        """Fail a turn whose stream carried frames we could read none of.

        A turn with no events is otherwise indistinguishable from a backend
        that had nothing to say, and the empty-answer note points the operator
        at the model rather than at the contract they actually got wrong.

        Keyed on *unmapped* frames, not merely "no events came out": a frame
        this backend recognizes may legitimately yield nothing (a lifecycle
        marker, a heartbeat), and failing those would be a false positive.
        """
        if state.get(_UNMAPPED_FRAMES) and not state.get("mapped_events"):
            raise AgentLoopError(
                t(
                    "agent_loop.stream_not_understood",
                    backend=self.name,
                    frames=state.get(_FRAMES_SEEN, 0),
                ),
                backend=self.name,
            )

    def _sse_data_events(
        self,
        data_lines: list[str],
        state: dict[str, Any],
        event_name: str = "",
    ) -> list[AgentLoopEvent]:
        if not data_lines:
            return []
        merged = "\n".join(data_lines)
        if len(merged) > MAX_LINE_BYTES:
            logger.warning(
                "agent-loop %s: dropping oversized SSE event (%d chars)",
                self.name,
                len(merged),
            )
            return []
        events: list[AgentLoopEvent] = []
        for candidate in (merged, *data_lines):
            try:
                obj = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                # The SSE line's own name is the last-resort type marker: only
                # set when the payload carries none of its own, so a vendor
                # that already names the kind keeps its own value.
                if event_name and not obj.get("type") and not obj.get("event"):
                    obj["event"] = event_name
                event = self._map_frame(obj, state)
                if event is not None:
                    events.append(event)
            break  # first parseable interpretation wins
        return events


async def _capped_lines(response: httpx.Response, backend_name: str) -> AsyncIterator[str]:
    """Yield decoded lines from the response body, bounding each line's size.

    ``aiter_lines`` buffers an unbounded line in memory before yielding, so
    the cap is enforced at the byte-buffer level: a runaway line is
    discarded (with a warning) and iteration continues with the next one.
    """
    buffer = bytearray()
    discarding = False
    async for chunk in response.aiter_bytes(65536):
        buffer.extend(chunk)
        while True:
            newline = buffer.find(b"\n")
            if newline < 0:
                if len(buffer) > MAX_LINE_BYTES:
                    logger.warning(
                        "agent-loop %s: stream line exceeds %d bytes, discarding it",
                        backend_name,
                        MAX_LINE_BYTES,
                    )
                    buffer.clear()
                    discarding = True
                break
            line = bytes(buffer[:newline])
            del buffer[: newline + 1]
            if discarding:
                # Tail of the discarded line; skip up to and past its newline.
                discarding = False
                continue
            if len(line) > MAX_LINE_BYTES:
                logger.warning(
                    "agent-loop %s: stream line exceeds %d bytes, discarding it",
                    backend_name,
                    MAX_LINE_BYTES,
                )
                continue
            yield line.decode("utf-8", "replace").rstrip("\r")
    if not discarding and buffer and len(buffer) <= MAX_LINE_BYTES:
        yield buffer.decode("utf-8", "replace").rstrip("\r")


class RunsAgentLoopBackend(HttpAgentLoopBackend):
    """Intellect run-endpoints variant: POST /v1/runs, then subscribe.

    Wire flow (Intellect api_server): ``POST {url}/v1/runs`` starts a run and
    returns ``run_id`` (202); ``GET {url}/v1/runs/{run_id}/events`` streams
    structured lifecycle events as SSE. Approvals resolve through
    ``POST .../approval`` and cancellation through ``POST .../stop`` — this
    is the HTTP family's only control-capable transport.

    The event stream is a **single subscription**: if the SSE connection
    drops, the run's event queue is torn down server-side and intermediate
    events are lost. On disconnect this backend degrades to polling
    ``GET {url}/v1/runs/{run_id}`` until a terminal status (which still
    carries the final output), surfacing a note that live events were lost.
    """

    supports_control = True

    #: Intellect's run control plane resolves a pending approval after five
    #: minutes and lets a pending clarify lapse after two, whatever KAGWeb
    #: waits for. Waiting longer can only produce "the user answered, but the
    #: agent had already moved on", so the operator's setting is clamped to
    #: these.
    approval_timeout_limit = 300
    clarify_timeout_limit = 120

    def __init__(
        self,
        *,
        name: str,
        url: str,
        turn_path: str,
        api_key: str,
        headers: dict[str, str],
        timeout_seconds: float,
        transport: httpx.AsyncBaseTransport | None = None,
        model: str = "",
        identity_mode: str = "off",
        tenant_id: str = "",
    ) -> None:
        super().__init__(
            name=name,
            url=url,
            turn_path=turn_path or "/v1/runs",
            api_key=api_key,
            headers=headers,
            timeout_seconds=timeout_seconds,
            transport=transport,
            model=model,
            identity_mode=identity_mode,
            tenant_id=tenant_id,
        )
        self._run_id = ""

    # -- URL helpers ---------------------------------------------------------

    def _runs_url(self, *suffix: str) -> str:
        path = "/".join(_path_segment(part) for part in suffix)
        return f"{self.url}{self.turn_path}{'/' + path if path else ''}"

    def _clarify_url(self) -> str:
        """The clarify endpoint: run-scoped, like the approval one.

        Intellect resolves a pending question through
        ``POST /v1/runs/{run_id}/clarify``. It used to be addressed as
        ``/v1/chat/completions/{session_id}/clarify``, which only one build
        exposes: the community ``api_server`` the ``intellect-runs`` preset
        targets registers the run-scoped route and nothing under
        ``chat/completions``, so every answer 404'd there. Clarify shares the
        run's identity, so the run-scoped route is the one that works wherever
        the run API does.
        """
        return self._runs_url(self._run_id, "clarify")

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/json, text/event-stream",
            **self.headers,
            **self._tenant_headers(),
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    # -- one turn ------------------------------------------------------------

    async def run(self, request: AgentLoopRequest) -> AsyncIterator[AgentLoopEvent]:
        self._apply_profile_identity()
        payload: dict[str, Any] = {"input": request.prompt}
        if request.history:
            payload["conversation_history"] = request.history
        session_id = _namespaced_session(self.session_prefix, request.session_id)
        if session_id:
            payload["session_id"] = session_id
        turn_model = (request.model or self.model).strip()
        if turn_model:
            # Omitted when unset: an existing deployment's body is unchanged.
            # Per-turn override wins over the profile model; the runs endpoint
            # consuming ``model`` is upstream-dependent (see the presets'
            # per_turn_model flag) — services that ignore it keep their own
            # default silently.
            payload["model"] = turn_model
        state: dict[str, Any] = {"buf": [], "terminal": False}
        timeout = httpx.Timeout(
            connect=15.0,
            read=self.timeout_seconds or 300.0,
            write=60.0,
            pool=15.0,
        )
        try:
            async with (
                asyncio.timeout(self.timeout_seconds) if self.timeout_seconds else nullcontext()
            ):
                async with httpx.AsyncClient(timeout=timeout, transport=self._transport) as client:
                    start = await client.post(
                        self._runs_url(), json=payload, headers=self._headers()
                    )
                    if start.status_code not in {200, 202}:
                        body = start.text[:_ERROR_BODY_LIMIT]
                        raise AgentLoopError(
                            t(
                                "agent_loop.http_status",
                                backend=self.name,
                                status=start.status_code,
                                detail=body[:400],
                            ),
                            backend=self.name,
                        )
                    self._run_id = str(start.json().get("run_id") or "")
                    if not self._run_id:
                        raise AgentLoopError(
                            t("agent_loop.runs_no_run_id", backend=self.name),
                            backend=self.name,
                        )
                    async for event in self._stream_events(
                        client, run_id=self._run_id, state=state
                    ):
                        state["mapped_events"] = state.get("mapped_events", 0) + 1
                        yield event
                    if not state["terminal"]:
                        # Polling output is deliberately NOT counted as mapped
                        # events: it comes from the status endpoint, not from
                        # the event stream, so counting it would let an
                        # unreadable stream pass the guard below — the poll
                        # always synthesizes at least one frame.
                        async for event in self._poll_terminal(client, state=state):
                            yield event
                    self._assert_stream_was_understood(state)
        except TimeoutError as exc:
            raise AgentLoopError(
                t("agent_loop.timeout", backend=self.name, seconds=int(self.timeout_seconds)),
                backend=self.name,
            ) from exc
        except httpx.HTTPError as exc:
            raise AgentLoopError(
                t("agent_loop.http_failed", backend=self.name, error=str(exc)),
                backend=self.name,
            ) from exc
        finally:
            # The run lives on the server, not in this connection: losing the
            # subscription does not stop it. So any exit that is not a terminal
            # event — the user cancelling the turn, the consumer closing this
            # generator, or the stream failing — has to stop the remote run
            # explicitly, or the agent keeps working (and spending) on a turn
            # nobody is waiting for.
            if not state["terminal"]:
                await self._stop_remote_run()

    async def _stop_remote_run(self) -> None:
        """Best-effort ``POST /v1/runs/{id}/stop``; never raises.

        This runs on the teardown path, including inside ``CancelledError``
        unwinding, so a failure here must not replace the real outcome.
        """
        if not self._run_id:
            return
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(connect=5.0, read=5.0, write=5.0, pool=5.0),
                transport=self._transport,
            ) as client:
                await client.post(self._runs_url(self._run_id, "stop"), headers=self._headers())
        except (httpx.HTTPError, asyncio.CancelledError):
            logger.debug("agent-loop %s: could not stop remote run", self.name)

    async def _stream_events(
        self, client: httpx.AsyncClient, *, run_id: str, state: dict[str, Any]
    ) -> AsyncIterator[AgentLoopEvent]:
        """Subscribe once to the run's SSE stream; translate lifecycle events."""
        try:
            async with client.stream(
                "GET", self._runs_url(run_id, "events"), headers=self._headers()
            ) as response:
                if response.status_code != 200:
                    return  # degrade to polling (e.g. the subscribe race)
                data_lines: list[str] = []
                event_name = ""
                async for line in _capped_lines(response, self.name):
                    if line.startswith(":"):
                        continue  # comment / keepalive
                    if not line.strip():
                        # Blank line terminates the current SSE event.
                        if data_lines:
                            for event in self._run_event_from_lines(data_lines, state, event_name):
                                yield event
                            data_lines = []
                            event_name = ""
                        continue
                    if line.startswith("data:"):
                        data_lines.append(line[len("data:") :].lstrip())
                    elif line.startswith("event:"):
                        event_name = line[len("event:") :].strip()
                if data_lines:
                    for event in self._run_event_from_lines(data_lines, state, event_name):
                        yield event
        except httpx.HTTPError:
            # The queue is torn down server-side once the SSE drops — degrade
            # to status polling for the terminal state.
            return

    async def _poll_terminal(
        self, client: httpx.AsyncClient, *, state: dict[str, Any]
    ) -> AsyncIterator[AgentLoopEvent]:
        """Degraded mode: poll run status until terminal.

        Capped at ``_POLL_MAX_ITERATIONS`` rounds: normally the outer
        per-turn wall clock bounds this, but a backend built without a
        timeout must not poll forever.
        """
        for _ in range(_POLL_MAX_ITERATIONS):
            if state["terminal"]:
                return
            await asyncio.sleep(2.0)
            try:
                status = await client.get(self._runs_url(self._run_id), headers=self._headers())
            except httpx.HTTPError:
                continue
            if status.status_code != 200:
                continue
            body = status.json()
            state_status = str(body.get("status") or "")
            if state_status == "completed":
                state["terminal"] = True
                # This is the only place the answer still exists: the event
                # queue is torn down with the subscription, but the terminal
                # status carries the run's own ``output``. Dropping it here is
                # what made a single dropped connection look like an empty
                # turn.
                for event in self._terminal_content_events(body, state):
                    yield event
                yield AgentLoopEvent("progress", text="[runs] completed (status poll)")
                usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
                data = {
                    "input_tokens": usage.get("input_tokens"),
                    "output_tokens": usage.get("output_tokens"),
                    "total_tokens": usage.get("total_tokens"),
                }
                yield AgentLoopEvent("usage", data=data)
            elif state_status in {"failed", "cancelled"}:
                state["terminal"] = True
                yield AgentLoopEvent(
                    "error",
                    text=str(body.get("error") or f"run {state_status}"),
                )
        if not state["terminal"]:
            yield AgentLoopEvent(
                "error",
                text=t("agent_loop.runs_poll_gave_up", backend=self.name),
            )

    def _run_event_from_lines(
        self, data_lines: list[str], state: dict[str, Any], event_name: str = ""
    ) -> list[AgentLoopEvent]:
        try:
            obj = json.loads("\n".join(data_lines))
        except json.JSONDecodeError:
            return []
        if isinstance(obj, dict):
            # The SSE line's name is the last-resort type marker, used only
            # when the payload carries none of its own.
            if event_name and not obj.get("type") and not obj.get("event"):
                obj["event"] = event_name
            return self._translate_run_event(obj, state)
        return []

    def _translate_run_event(
        self, obj: dict[str, Any], state: dict[str, Any]
    ) -> list[AgentLoopEvent]:
        """Map one run-channel event onto the neutral schema.

        The channel is defined by Intellect's Rust ``api_server``, which is the
        authoritative implementation. Its wire shape is a ``RunEvent`` —
        ``{"event": <channel>, ...payload}`` — and the payload's own ``type`` is
        what actually separates content kinds: ``assistant.delta``,
        ``reasoning.delta``, ``thinking.progress``, ``assistant.completed`` and
        ``interim_assistant`` all arrive under ``event="message.delta"``, and
        both tool transitions arrive under ``event="tool.progress"``.

        So dispatch on ``type`` first and fall back to ``event`` — the same
        order ``cli_backend.translate_generic`` already uses for vendor frames.
        Reading ``event`` alone silently drops text, reasoning, tools and
        approvals (only the ``event``-named terminal states survive), and
        because this method used to end in a bare ``return []`` there was no
        diagnostic to show it.
        """
        kind = str(obj.get("type") or "").strip() or str(obj.get("event") or "").strip()
        state[_FRAMES_SEEN] = state.get(_FRAMES_SEEN, 0) + 1

        # -- assistant text --------------------------------------------------
        if kind in {"assistant.delta", "message.delta"}:
            # ``message.delta`` is the legacy channel name (older adapters emit
            # it with a ``delta`` field and no ``type``); the authoritative
            # shape carries ``text`` under ``type="assistant.delta"``.
            delta = str(obj.get("text") or obj.get("delta") or "")
            if delta:
                state["buf"].append(delta)
            return []

        # -- reasoning -------------------------------------------------------
        if kind in {"reasoning.delta", "reasoning.available"}:
            text = str(obj.get("text") or "")
            return [AgentLoopEvent("thinking", text=text)] if text else []

        # -- complete assistant blocks ---------------------------------------
        if kind in {"interim_assistant", "assistant.completed"}:
            events = self._flush_buffered_content(state)
            body = str(obj.get("content") or obj.get("response") or "")
            return self._append_block(events, body)

        # -- tools -----------------------------------------------------------
        if kind in {"tool.started", "tool.completed", "tool.failed"}:
            events = self._flush_buffered_content(state)
            name = str(obj.get("name") or obj.get("tool") or "tool")
            if kind == "tool.started":
                arguments = obj.get("arguments")
                if not isinstance(arguments, dict):
                    arguments = None
                events.append(
                    AgentLoopEvent(
                        "tool_call",
                        name=name,
                        text=str(obj.get("preview") or ""),
                        data={"args": arguments} if arguments is not None else {},
                    )
                )
            else:
                data: dict[str, Any] = {
                    "is_error": kind == "tool.failed" or bool(obj.get("error")),
                }
                duration = obj.get("duration_s")
                if isinstance(duration, (int, float)):
                    data["duration_s"] = float(duration)
                events.append(
                    AgentLoopEvent(
                        "tool_result",
                        name=name,
                        text=str(obj.get("result") or ""),
                        data=data,
                    )
                )
            return events

        # -- clarify (the agent asks the user a question mid-turn) -----------
        # Intellect's community api_server emits ``clarify.request`` (matching
        # the ``approval.request`` sibling); the bare ``clarify`` is kept for
        # adapters that use it. Reading only the bare form dropped every
        # clarify question before this card could ever be shown.
        if kind in {"clarify", "clarify.request"}:
            # Buffered narration stays buffered, exactly as for an approval:
            # it belongs to the answer that resumes after the user replies.
            return [
                AgentLoopEvent(
                    "clarify_request",
                    text=str(obj.get("question") or ""),
                    data={
                        "request_id": str(obj.get("clarify_id") or ""),
                        "choices": [str(choice) for choice in (obj.get("choices") or [])],
                    },
                )
            ]

        # -- approval --------------------------------------------------------
        if kind == "approval.request":
            # Buffered narration stays buffered: it flushes into a content
            # block when the run completes.
            return [
                AgentLoopEvent(
                    "approval_request",
                    name=str(obj.get("tool_name") or obj.get("tool") or "tool"),
                    # ``arguments`` is the authoritative field; ``preview`` is
                    # the legacy one.
                    text=_pretty_arguments(obj.get("arguments") or obj.get("preview")),
                    data={
                        # The runs API resolves approvals per run, not per request.
                        "request_id": str(obj.get("run_id") or self._run_id),
                        "choices": [
                            str(choice) for choice in (obj.get("choices") or APPROVAL_CHOICES)
                        ],
                    },
                )
            ]

        # -- run lifecycle ---------------------------------------------------
        if kind == "run.started":
            return []
        if kind == "run.completed":
            # Reconciles the buffered deltas with the run's ``output`` rather
            # than flushing then appending: the two carry the same text, so
            # appending both emits the answer twice. See
            # `_terminal_content_events`.
            events = self._terminal_content_events(obj, state)
            state["terminal"] = True
            usage = obj.get("usage") if isinstance(obj.get("usage"), dict) else {}
            usage_data = {
                "input_tokens": usage.get("input_tokens"),
                "output_tokens": usage.get("output_tokens"),
                "total_tokens": usage.get("total_tokens"),
            }
            if any(value is not None for value in usage_data.values()):
                events.append(AgentLoopEvent("usage", data=usage_data))
            # A run can complete *without finishing*: the agent stopped at a
            # generation ceiling and the text above is a prefix. The service
            # marks that with ``completed: false`` / ``partial: true``, which
            # this channel used to drop entirely — so a truncated answer
            # reached the user labelled as the finished one. Surfaced as the
            # same machine-readable reason the ACP family emits, so the UI's
            # "stopped short" marker works on both transports. Absent fields
            # (older services) mean a clean finish, which is the default.
            reason = _short_finish_reason(obj)
            if reason:
                events.append(
                    AgentLoopEvent(
                        "error",
                        text=t("agent_loop.acp_turn_incomplete", reason=reason),
                        data={"stop_reason": _stop_reason_code(obj)},
                    )
                )
            return events
        if kind == "run.failed":
            state["terminal"] = True
            return [AgentLoopEvent("error", text=str(obj.get("error") or "run failed"))]
        if kind == "run.cancelled":
            state["terminal"] = True
            return [AgentLoopEvent("error", text="run cancelled")]

        # -- recognised no-ops -----------------------------------------------
        # ``thinking.progress`` is a heartbeat (elapsed/silent only, no text);
        # ``run.stopping`` precedes the cancelled terminal; ``approval.responded``
        # echoes a decision we already surfaced. Handled explicitly so they do
        # not look like unknown traffic in the debug log.
        if kind in {"thinking.progress", "run.stopping", "approval.responded"}:
            return []

        # -- events the transport itself reports -----------------------------
        # A lagging subscriber: the run's broadcast buffer overflowed and the
        # server dropped *n* events. The turn can still finish, but the trace
        # is now incomplete, and saying so is the only way a user can tell the
        # difference between "nothing happened" and "we stopped being told".
        if kind == "lagged":
            missed = obj.get("missed")
            return [
                AgentLoopEvent(
                    "progress",
                    text=t(
                        "agent_loop.run_events_lagged",
                        backend=self.name,
                        missed=missed if isinstance(missed, int) else 0,
                    ),
                )
            ]

        # ``error``: the run channel normally turns a provider failure into
        # ``run.failed`` (the agent loop returns the error rather than
        # forwarding it), so this is a defensive mapping for the frame as it is
        # declared on the channel: ``event: error`` with ``{"message": …}``.
        if kind == "error":
            return [AgentLoopEvent("error", text=str(obj.get("message") or "stream error"))]

        # Unknown shapes are the one case that must leave a trace: silently
        # dropping them is what let the field-name mismatches above go
        # unnoticed for so long.
        #
        # Only the labels are logged, never the body. An unrecognised event is
        # still an event, and this channel's payloads carry model text
        # (``output``), tool results (``result``) and tool arguments — logging
        # the object would write conversation content to the log file. The
        # labels are bounded too, since they come from the remote backend.
        logger.debug(
            "agent-loop %s: unrecognised run event (type=%.100s event=%.100s)",
            self.name,
            str(obj.get("type") or ""),
            str(obj.get("event") or ""),
        )
        state[_UNMAPPED_FRAMES] = state.get(_UNMAPPED_FRAMES, 0) + 1
        self._reject_unmappable_frame(obj)
        return []

    @staticmethod
    def _flush_buffered_content(state: dict[str, Any]) -> list[AgentLoopEvent]:
        """Drain the delta buffer into one content block (or nothing)."""
        if not state["buf"]:
            return []
        events = [AgentLoopEvent("content", text="".join(state["buf"]))]
        state["buf"] = []
        return events

    def _terminal_content_events(
        self, obj: dict[str, Any], state: dict[str, Any]
    ) -> list[AgentLoopEvent]:
        """The content blocks for a terminal frame, without repeating text.

        Intellect reports the finished answer twice on this channel: as
        ``StreamEvent::TextDelta`` chunks while it is generated, and again as
        the run's ``output`` when it completes. Flushing the buffer *and*
        appending ``output`` therefore emits the same paragraph twice, and the
        caller joins content blocks with a blank line — so the answer reads
        ``"Hello world\\n\\nHello world"``.

        Both terminal paths route through here: the ``run.completed`` event and
        the degraded status poll, which reads the same ``output`` out of the
        status body.
        """
        events = self._flush_buffered_content(state)
        return self._append_block(events, str(obj.get("output") or ""))

    @staticmethod
    def _append_block(events: list[AgentLoopEvent], text: str) -> list[AgentLoopEvent]:
        """Append one content block unless the text is already accounted for.

        Text reaches this client by more than one route — streamed deltas, the
        terminal ``output``, and a completed-message body — and the routes
        overlap: deltas spell out the same message that ``output`` later
        restates. So a full message is reconciled against what has already been
        emitted rather than appended blindly:

        * equal to the last block → it is a restatement, drop it;
        * an extension of the last block → a truncated stream, append only the
          missing tail so the joined text equals the answer;
        * unrelated → earlier narration for a different step, keep it.

        The comparison is against the **last** block only. An agentic turn
        emits one block per step (each tool transition flushes the narration
        before it), and the message being restated is always the most recent
        one — comparing against every block would discard legitimately
        repeated text such as a recurring status line.
        """
        if not text:
            return events
        if not events:
            events.append(AgentLoopEvent("content", text=text))
            return events
        last = events[-1].text
        if last == text:
            return events
        if text.startswith(last):
            remainder = text[len(last) :]
            if remainder:
                events[-1] = AgentLoopEvent("content", text=f"{last}{remainder}")
            return events
        events.append(AgentLoopEvent("content", text=text))
        return events

    # -- control plane -------------------------------------------------------

    async def respond_approval(self, request_id: str, choice: str) -> None:
        if not self._run_id:
            return
        async with httpx.AsyncClient(transport=self._transport) as client:
            await client.post(
                self._runs_url(self._run_id, "approval"),
                json={"choice": choice},
                headers=self._headers(),
            )

    async def respond_clarify(self, request_id: str, answer: str) -> None:
        """Deliver the user's answer to an in-flight ``clarify``.

        The server is blocked inside the run's clarify callback, so resolving
        it lets the run continue. The body field is ``response`` — the name the
        issue's proposed contract uses, and the preferred one; both Intellect
        implementations accept ``answer`` as an alias on this route.
        """
        if not self._run_id:
            # Nothing to address: the endpoint is keyed by the run, so without
            # one the request is a guaranteed 404. Skip it rather than add
            # noise to the log.
            logger.debug(
                "agent-loop %s: cannot answer clarify %s without a run id",
                self.name,
                request_id,
            )
            return
        async with httpx.AsyncClient(transport=self._transport) as client:
            await client.post(
                self._clarify_url(),
                json={"response": answer, "clarify_id": request_id},
                headers=self._headers(),
            )

    async def cancel(self) -> None:
        if not self._run_id:
            return
        async with httpx.AsyncClient(transport=self._transport) as client:
            await client.post(self._runs_url(self._run_id, "stop"), headers=self._headers())
