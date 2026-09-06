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

import httpx

from kagweb.services.i18n import t

from .cli_backend import translate_generic
from .protocol import (
    EVENT_KINDS,
    MAX_LINE_BYTES,
    AgentLoopBackend,
    AgentLoopError,
    AgentLoopEvent,
    AgentLoopRequest,
)

logger = logging.getLogger(__name__)

_ERROR_BODY_LIMIT = 2000


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
                logger.debug("agent-loop %s: non-JSON stream line: %.200s", self.name, text)
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
