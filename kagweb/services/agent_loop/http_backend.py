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
    ) -> None:
        self.name = name
        self.url = url.rstrip("/")
        self.turn_path = turn_path if turn_path.startswith("/") else f"/{turn_path}"
        self.api_key = str(api_key or "")
        self.headers = {str(k): str(v) for k, v in (headers or {}).items()}
        self.timeout_seconds = float(timeout_seconds) if timeout_seconds else 0.0
        # Test/embedding hook: injected httpx transport (never set by the
        # settings-driven factory).
        self._transport = transport

    def endpoint(self) -> str:
        return f"{self.url}{self.turn_path}"

    async def run(self, request: AgentLoopRequest) -> AsyncIterator[AgentLoopEvent]:
        payload: dict[str, Any] = {
            "session_id": request.session_id,
            "language": request.language,
            "prompt": request.prompt,
            "history": request.history,
        }
        headers = {
            "Accept": "text/event-stream, application/x-ndjson, application/jsonl",
            **self.headers,
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
                            yield event
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
                event = _neutral_event(obj, state)
                if event is not None:
                    yield event

    async def _iter_sse(
        self, response: httpx.Response, state: dict[str, Any]
    ) -> AsyncIterator[AgentLoopEvent]:
        data_lines: list[str] = []
        async for line in _capped_lines(response, self.name):
            if line.startswith(":"):
                continue  # SSE comment / keepalive
            if not line.strip():
                # Blank line terminates the current event, if any data.
                for event in self._sse_data_events(data_lines, state):
                    yield event
                data_lines = []
                continue
            if line.startswith("data:"):
                data_lines.append(line[len("data:") :].lstrip())
        for event in self._sse_data_events(data_lines, state):
            yield event

    def _sse_data_events(
        self, data_lines: list[str], state: dict[str, Any]
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
                event = _neutral_event(obj, state)
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
    ) -> None:
        super().__init__(
            name=name,
            url=url,
            turn_path=turn_path or "/v1/runs",
            api_key=api_key,
            headers=headers,
            timeout_seconds=timeout_seconds,
            transport=transport,
        )
        self._run_id = ""
        #: The server's own session id, captured from the event stream. The
        #: clarify endpoint is keyed by it (not by the KAGWeb session id).
        self._session_id = ""

    # -- URL helpers ---------------------------------------------------------

    def _runs_url(self, *suffix: str) -> str:
        path = "/".join(_path_segment(part) for part in suffix)
        return f"{self.url}{self.turn_path}{'/' + path if path else ''}"

    def _clarify_url(self) -> str:
        """The clarify endpoint, which is NOT under ``turn_path``.

        Intellect exposes it as ``POST /v1/chat/completions/{session_id}/clarify``
        — a sibling of the runs API, keyed by session rather than by run.
        """
        return f"{self.url}/v1/chat/completions/{_path_segment(self._session_id)}/clarify"

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json, text/event-stream", **self.headers}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    # -- one turn ------------------------------------------------------------

    async def run(self, request: AgentLoopRequest) -> AsyncIterator[AgentLoopEvent]:
        payload: dict[str, Any] = {"input": request.prompt}
        if request.history:
            payload["conversation_history"] = request.history
        if request.session_id:
            payload["session_id"] = request.session_id
        state: dict[str, Any] = {"buf": [], "terminal": False}
        # Seed from the request so a clarify can still be answered when the
        # stream never reports the server's own session id; an authoritative
        # value from ``run.started`` / ``assistant.completed`` overrides it.
        self._session_id = request.session_id or ""
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
                        yield event
                    if not state["terminal"]:
                        async for event in self._poll_terminal(client, state=state):
                            yield event
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
                async for line in _capped_lines(response, self.name):
                    if line.startswith(":"):
                        continue  # comment / keepalive
                    if not line.strip():
                        # Blank line terminates the current SSE event.
                        if data_lines:
                            for event in self._run_event_from_lines(data_lines, state):
                                yield event
                            data_lines = []
                        continue
                    if line.startswith("data:"):
                        data_lines.append(line[len("data:") :].lstrip())
                if data_lines:
                    for event in self._run_event_from_lines(data_lines, state):
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
        self, data_lines: list[str], state: dict[str, Any]
    ) -> list[AgentLoopEvent]:
        try:
            obj = json.loads("\n".join(data_lines))
        except json.JSONDecodeError:
            return []
        if isinstance(obj, dict):
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
            self._remember_session(obj)
            body = str(obj.get("content") or obj.get("response") or "")
            if body:
                events.append(AgentLoopEvent("content", text=body))
            return events

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
        if kind == "clarify":
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
                    text=str(obj.get("arguments") or obj.get("preview") or ""),
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
            self._remember_session(obj)
            return []
        if kind == "run.completed":
            events = self._flush_buffered_content(state)
            self._remember_session(obj)
            state["terminal"] = True
            output = str(obj.get("output") or "")
            if output:
                events.append(AgentLoopEvent("content", text=output))
            usage = obj.get("usage") if isinstance(obj.get("usage"), dict) else {}
            usage_data = {
                "input_tokens": usage.get("input_tokens"),
                "output_tokens": usage.get("output_tokens"),
                "total_tokens": usage.get("total_tokens"),
            }
            if any(value is not None for value in usage_data.values()):
                events.append(AgentLoopEvent("usage", data=usage_data))
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
        return []

    @staticmethod
    def _flush_buffered_content(state: dict[str, Any]) -> list[AgentLoopEvent]:
        """Drain the delta buffer into one content block (or nothing)."""
        if not state["buf"]:
            return []
        events = [AgentLoopEvent("content", text="".join(state["buf"]))]
        state["buf"] = []
        return events

    def _remember_session(self, obj: dict[str, Any]) -> None:
        """Keep the server's session id, which the clarify endpoint addresses.

        The run channel reports it on ``run.started`` / ``assistant.completed``;
        it is the *Intellect* session, which is what ``/v1/chat/completions/
        {session_id}/clarify`` is keyed by — not the KAGWeb session id we send
        when starting the run.
        """
        session_id = str(obj.get("session_id") or "")
        if session_id:
            self._session_id = session_id

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

        The clarify endpoint is NOT under the run path; see ``_clarify_url``.
        """
        if not self._session_id:
            # Nothing to address: without a server session id the request would
            # be a guaranteed 404, so skip it rather than add noise.
            logger.debug(
                "agent-loop %s: cannot answer clarify %s without a server session id",
                self.name,
                request_id,
            )
            return
        async with httpx.AsyncClient(transport=self._transport) as client:
            await client.post(
                self._clarify_url(),
                json={"clarify_id": request_id, "answer": answer},
                headers=self._headers(),
            )

    async def cancel(self) -> None:
        if not self._run_id:
            return
        async with httpx.AsyncClient(transport=self._transport) as client:
            await client.post(self._runs_url(self._run_id, "stop"), headers=self._headers())
