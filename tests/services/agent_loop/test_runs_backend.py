"""RunsAgentLoopBackend: run-start, SSE event mapping, approvals, degrade."""

from __future__ import annotations

import json

import httpx
import pytest

from kagweb.services.agent_loop.http_backend import RunsAgentLoopBackend
from kagweb.services.agent_loop.protocol import AgentLoopRequest

pytestmark = pytest.mark.asyncio

SSE_BODY = (
    'data: {"event": "message.delta", "delta": "Hello "}\n'
    "\n"
    'data: {"event": "message.delta", "delta": "world"}\n'
    "\n"
    'data: {"event": "tool.started", "tool": "shell", "preview": "ls"}\n'
    "\n"
    'data: {"event": "tool.completed", "tool": "shell"}\n'
    "\n"
    'data: {"event": "approval.request", "tool": "shell",'
    ' "choices": ["once", "deny"]}\n'
    "\n"
    'data: {"event": "run.completed", "output": "final answer",'
    ' "usage": {"input_tokens": 1, "output_tokens": 2, "total_tokens": 3}}\n'
    "\n"
    ": stream closed\n"
)


def _mock_backend(calls: list[httpx.Request], sse_body: str = SSE_BODY):
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        path = request.url.path
        if path.endswith("/v1/runs") and request.method == "POST":
            return httpx.Response(202, json={"run_id": "run_1", "status": "started"})
        if path.endswith("/events"):
            return httpx.Response(200, text=sse_body, headers={"content-type": "text/event-stream"})
        if path.endswith("/approval"):
            return httpx.Response(200, json={"resolved": 1})
        if path.endswith("/stop"):
            return httpx.Response(200, json={"stopped": True})
        if path.endswith("/runs/run_1"):
            return httpx.Response(
                200,
                json={
                    "status": "completed",
                    "output": "polled answer",
                    "usage": {"input_tokens": 1, "output_tokens": 2, "total_tokens": 3},
                },
            )
        return httpx.Response(404, json={"error": "not found"})

    return RunsAgentLoopBackend(
        name="intellect-runs",
        url="http://gateway.test",
        turn_path="/v1/runs",
        api_key="key-1",
        headers={},
        timeout_seconds=30,
        transport=httpx.MockTransport(handler),
    )


async def test_run_stream_maps_events_and_forwards_approval(tmp_path) -> None:
    calls: list[httpx.Request] = []
    backend = _mock_backend(calls)

    events = []
    agen = backend.run(
        AgentLoopRequest(
            prompt="hi", session_id="s1", history=[{"role": "user", "content": "earlier"}]
        )
    )
    async for event in agen:
        events.append(event)
        if event.kind == "approval_request":
            await backend.respond_approval(event.data["request_id"], "once")

    kinds = [event.kind for event in events]
    # deltas buffer into one block, flushed at the tool boundary
    assert kinds == [
        "content",
        "tool_call",
        "tool_result",
        "approval_request",
        "content",
        "usage",
    ]
    assert events[0].text == "Hello world"
    assert events[1].name == "shell"
    assert events[3].data["request_id"] == "run_1"  # approvals resolve per run
    assert events[3].data["choices"] == ["once", "deny"]
    assert events[4].text == "final answer"
    assert events[5].data == {"input_tokens": 1, "output_tokens": 2, "total_tokens": 3}

    # the start request carried the prompt and the history
    start = calls[0]
    body = json.loads(start.content)
    assert body["input"] == "hi"
    assert body["conversation_history"] == [{"role": "user", "content": "earlier"}]
    assert start.headers["Authorization"] == "Bearer key-1"
    # the approval decision reached the control endpoint with our vocabulary
    approval = next(call for call in calls if call.url.path.endswith("/approval"))
    assert json.loads(approval.content) == {"choice": "once"}


async def test_stream_failure_degrades_to_status_polling() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        path = request.url.path
        if path.endswith("/v1/runs") and request.method == "POST":
            return httpx.Response(202, json={"run_id": "run_1"})
        if path.endswith("/events"):
            return httpx.Response(404, json={"error": "run not found"})
        if path.endswith("/runs/run_1"):
            return httpx.Response(
                200,
                json={
                    "status": "completed",
                    "output": "polled answer",
                    "usage": {"input_tokens": 1, "output_tokens": 2, "total_tokens": 3},
                },
            )
        return httpx.Response(404, json={"error": "not found"})

    backend = RunsAgentLoopBackend(
        name="intellect-runs",
        url="http://gateway.test",
        turn_path="/v1/runs",
        api_key="",
        headers={},
        timeout_seconds=30,
        transport=httpx.MockTransport(handler),
    )
    events = [event async for event in backend.run(AgentLoopRequest(prompt="hi", session_id="s2"))]
    kinds = [event.kind for event in events]
    assert "progress" in kinds  # the "completed (status poll)" note
    assert events[-1].data["total_tokens"] == 3
    # the run status was polled, no live events were available
    assert any(call.url.path.endswith("/runs/run_1") and call.method == "GET" for call in calls)
