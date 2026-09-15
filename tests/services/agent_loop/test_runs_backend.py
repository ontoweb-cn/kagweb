"""RunsAgentLoopBackend: run-start, SSE event mapping, approvals, degrade."""

from __future__ import annotations

import json

import httpx
import pytest

from kagweb.services.agent_loop.http_backend import RunsAgentLoopBackend
from kagweb.services.agent_loop.protocol import AgentLoopRequest

pytestmark = pytest.mark.asyncio

# The authoritative wire shape, taken from Intellect's Rust api_server (the
# implementation that owns this channel). Every frame is a RunEvent carrying
# ``event`` (the SSE channel) plus a flattened payload whose ``type`` is what
# actually distinguishes content kinds — text, reasoning and the completions
# all ride ``event="message.delta"``, and both tool transitions ride
# ``event="tool.progress"``. Dispatching on ``event`` alone therefore loses
# text, reasoning and tools; this fixture exists to pin the ``type``-first
# reading, and replaces an earlier one that encoded the same wrong assumption
# as the code under test (so it passed either way).
SSE_BODY = (
    'data: {"event": "run.started", "session_id": "srv-session", "run_id": "run_1"}\n'
    "\n"
    'data: {"event": "message.delta", "type": "reasoning.delta", "text": "weighing options"}\n'
    "\n"
    'data: {"event": "message.delta", "type": "thinking.progress",'
    ' "elapsed_s": 1.2, "silent_s": 0.4}\n'
    "\n"
    'data: {"event": "message.delta", "type": "assistant.delta", "text": "Hello "}\n'
    "\n"
    'data: {"event": "message.delta", "type": "assistant.delta", "text": "world"}\n'
    "\n"
    'data: {"event": "tool.progress", "type": "tool.started",'
    ' "name": "shell", "arguments": {"command": "ls"}, "tool_id": "t1"}\n'
    "\n"
    'data: {"event": "tool.progress", "type": "tool.completed",'
    ' "name": "shell", "result": "a.txt", "duration_s": 0.25, "tool_id": "t1"}\n'
    "\n"
    'data: {"event": "approval.request", "tool_name": "shell",'
    ' "arguments": "rm -rf build", "choices": ["once", "deny"]}\n'
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
        if path.endswith("/clarify"):
            return httpx.Response(200, json={"answered": 1})
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
        "thinking",
        "content",
        "tool_call",
        "tool_result",
        "approval_request",
        "content",
        "usage",
    ]
    assert events[0].text == "weighing options"
    assert events[1].text == "Hello world"
    assert events[2].name == "shell"
    assert events[2].data["args"] == {"command": "ls"}
    assert events[3].name == "shell"
    assert events[3].text == "a.txt"
    assert events[3].data["duration_s"] == 0.25
    assert events[4].data["request_id"] == "run_1"  # approvals resolve per run
    assert events[4].data["choices"] == ["once", "deny"]
    assert events[5].text == "final answer"
    assert events[6].data == {"input_tokens": 1, "output_tokens": 2, "total_tokens": 3}

    # the start request carried the prompt and the history
    start = calls[0]
    body = json.loads(start.content)
    assert body["input"] == "hi"
    assert body["conversation_history"] == [{"role": "user", "content": "earlier"}]
    assert start.headers["Authorization"] == "Bearer key-1"
    # the approval decision reached the control endpoint with our vocabulary
    approval = next(call for call in calls if call.url.path.endswith("/approval"))
    assert json.loads(approval.content) == {"choice": "once"}


async def test_approval_reads_the_authoritative_tool_fields(tmp_path) -> None:
    """Rust sends ``tool_name`` / ``arguments``; reading ``tool`` / ``preview``
    silently degraded every approval to a generic name and an empty preview."""
    calls: list[httpx.Request] = []
    backend = _mock_backend(calls)

    events = [event async for event in backend.run(AgentLoopRequest(prompt="hi"))]
    approval = next(event for event in events if event.kind == "approval_request")

    assert approval.name == "shell"
    assert approval.text == "rm -rf build"


async def test_clarify_is_surfaced_and_answered_on_its_own_endpoint(tmp_path) -> None:
    """A clarify is a question, not a decision: it must surface as a
    ``clarify_request`` and its answer must go to the run-scoped clarify
    endpoint.

    Intellect registers a run's pending question under the **run** id and
    refuses an id whose recorded owner differs, so the session-keyed
    ``/v1/chat/completions/{session_id}/clarify`` route — which serves the
    streaming chat flows, where the owner really is the session — can never
    resolve one."""
    body = SSE_BODY.replace(
        'data: {"event": "approval.request", "tool_name": "shell",'
        ' "arguments": "rm -rf build", "choices": ["once", "deny"]}\n'
        "\n",
        'data: {"event": "clarify", "type": "clarify", "clarify_id": "c-1",'
        ' "question": "Which database?", "choices": ["postgres", "sqlite"]}\n'
        "\n",
    )
    calls: list[httpx.Request] = []
    backend = _mock_backend(calls, sse_body=body)

    events = []
    async for event in backend.run(AgentLoopRequest(prompt="hi", session_id="s1")):
        events.append(event)
        if event.kind == "clarify_request":
            await backend.respond_clarify("c-1", "postgres")

    clarify = next(event for event in events if event.kind == "clarify_request")
    assert clarify.text == "Which database?"
    assert clarify.data["request_id"] == "c-1"
    assert clarify.data["choices"] == ["postgres", "sqlite"]
    # the stream resumes: the run still completes
    assert events[-1].kind in {"usage", "content"}

    answer = next(call for call in calls if call.url.path.endswith("/clarify"))
    # keyed by the run the answer belongs to — not by the session id
    assert answer.url.path == "/v1/runs/run_1/clarify"
    assert json.loads(answer.content) == {"clarify_id": "c-1", "answer": "postgres"}


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
    # The answer must survive the lost stream. The event queue is torn down
    # with the subscription server-side, so the terminal status is the only
    # remaining source of the run's output — a poll that reports usage but not
    # the text turns one dropped connection into an empty turn.
    assert "".join(event.text for event in events if event.kind == "content") == "polled answer"


# ── the remote ids are attacker-influenced; they must stay in their segment ──


def test_hostile_run_id_cannot_escape_its_path_segment() -> None:
    """`run_id` comes from the backend's start response and rides into URLs
    that carry the operator's Authorization header. Left raw, `../..` is
    normalised by httpx into a different path on the configured host."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(202, json={"run_id": "../../admin/secret"})

    backend = RunsAgentLoopBackend(
        name="intellect-runs",
        url="http://gateway.test",
        turn_path="/v1/runs",
        api_key="k",
        headers={},
        timeout_seconds=5,
        transport=httpx.MockTransport(handler),
    )
    backend._run_id = "../../admin/secret"

    url = backend._runs_url(backend._run_id, "events")
    # Every separator the value carried is percent-encoded, so the whole value
    # is ONE segment: the literal ".." is harmless without an unescaped "/".
    assert url == "http://gateway.test/v1/runs/..%2F..%2Fadmin%2Fsecret/events"


def test_hostile_run_id_cannot_escape_the_clarify_path() -> None:
    backend = RunsAgentLoopBackend(
        name="intellect-runs",
        url="http://gateway.test",
        turn_path="/v1/runs",
        api_key="k",
        headers={},
        timeout_seconds=5,
    )
    backend._run_id = "../../admin?x=1"

    url = backend._clarify_url()
    assert url == "http://gateway.test/v1/runs/..%2F..%2Fadmin%3Fx%3D1/clarify"
    # No query or fragment was split off.
    assert "?" not in url and "#" not in url


async def test_an_unknown_event_logs_its_labels_but_not_its_body(caplog) -> None:
    """Unrecognised events are worth a trace, but the payload carries model
    text, tool results and tool arguments — logging the object would write
    conversation content to the log file."""
    secret = "SENSITIVE-ANSWER-TEXT"
    calls: list[httpx.Request] = []
    body = (
        'data: {"event": "some.new.event", "type": "brand.new.type",'
        f' "output": "{secret}"}}\n'
        "\n"
        ": stream closed\n"
    )
    backend = _mock_backend(calls, sse_body=body)

    with caplog.at_level("DEBUG", logger="kagweb.services.agent_loop.http_backend"):
        [event async for event in backend.run(AgentLoopRequest(prompt="hi"))]

    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert "brand.new.type" in logged  # the labels are still reported
    assert secret not in logged  # the body is not


async def test_runs_request_carries_the_model_only_when_configured() -> None:
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/v1/runs") and request.method == "POST":
            bodies.append(json.loads(request.content))
            return httpx.Response(202, json={"run_id": "run_1"})
        if request.url.path.endswith("/events"):
            return httpx.Response(200, text="", headers={"content-type": "text/event-stream"})
        return httpx.Response(
            200, json={"status": "completed", "output": "x", "usage": {"total_tokens": 1}}
        )

    def build(**extra):
        return RunsAgentLoopBackend(
            name="intellect-runs",
            url="http://gateway.test",
            turn_path="/v1/runs",
            api_key="k",
            headers={},
            timeout_seconds=5,
            transport=httpx.MockTransport(handler),
            **extra,
        )

    [event async for event in build(model="gpt-5").run(AgentLoopRequest(prompt="hi"))]
    assert bodies[-1]["model"] == "gpt-5"

    [event async for event in build().run(AgentLoopRequest(prompt="hi"))]
    assert "model" not in bodies[-1]


async def test_runs_request_prefers_the_turn_model() -> None:
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/v1/runs") and request.method == "POST":
            bodies.append(json.loads(request.content))
            return httpx.Response(202, json={"run_id": "run_1"})
        if request.url.path.endswith("/events"):
            return httpx.Response(200, text="", headers={"content-type": "text/event-stream"})
        return httpx.Response(
            200, json={"status": "completed", "output": "x", "usage": {"total_tokens": 1}}
        )

    def build(**extra):
        return RunsAgentLoopBackend(
            name="intellect-runs",
            url="http://gateway.test",
            turn_path="/v1/runs",
            api_key="k",
            headers={},
            timeout_seconds=5,
            transport=httpx.MockTransport(handler),
            **extra,
        )

    [
        event
        async for event in build(model="profile-m").run(
            AgentLoopRequest(prompt="hi", model="turn-m")
        )
    ]
    assert bodies[-1]["model"] == "turn-m"

    # Without a profile model the per-turn pick still rides along.
    [event async for event in build().run(AgentLoopRequest(prompt="hi", model="turn-m"))]
    assert bodies[-1]["model"] == "turn-m"


# ── the finished answer is reported twice; it must be emitted once ──────────


def _completed_body(*, deltas: str, output: str, interim: str = "") -> str:
    """A run stream whose text arrives as deltas and again as ``output``.

    Intellect forwards the answer as ``TextDelta`` chunks while it is generated
    and then reports the same text as the run's ``output``; the caller joins
    content blocks with a blank line, so echoing both doubles the answer.
    """
    parts = [
        'data: {"event": "run.started", "session_id": "srv-session", "run_id": "run_1"}\n\n'
    ]
    for chunk in deltas:
        parts.append(
            f'data: {{"event": "message.delta", "type": "assistant.delta",'
            f' "text": "{chunk}"}}\n\n'
        )
    if interim:
        parts.append(
            f'data: {{"event": "message.delta", "type": "interim_assistant",'
            f' "content": "{interim}"}}\n\n'
        )
    parts.append(
        f'data: {{"event": "run.completed", "output": "{output}",'
        ' "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}}\n\n'
        ": stream closed\n"
    )
    return "".join(parts)


async def _content_of(body: str) -> str:
    calls: list[httpx.Request] = []
    backend = _mock_backend(calls, sse_body=body)
    events = [event async for event in backend.run(AgentLoopRequest(prompt="hi", session_id="s1"))]
    # Mirrors how the capability joins blocks into the persisted answer.
    return "\n\n".join(event.text for event in events if event.kind == "content")


async def test_a_plain_turn_does_not_repeat_its_answer() -> None:
    """No tool calls: the deltas already spell the whole answer, so the
    ``output`` echo must not become a second paragraph."""
    body = _completed_body(deltas=["Hello ", "world"], output="Hello world")
    assert await _content_of(body) == "Hello world"


async def test_a_tool_turn_keeps_its_narration_and_does_not_repeat_the_answer() -> None:
    """An agentic turn emits a block per step: the narration before a tool call
    is genuinely different text and must be kept. Only the *last* block is the
    one the terminal ``output`` restates, so reconciliation must not discard
    the earlier step's text along with the duplicate."""
    body = (
        'data: {"event": "run.started", "session_id": "srv-session", "run_id": "run_1"}\n\n'
        'data: {"event": "message.delta", "type": "assistant.delta",'
        ' "text": "Let me look."}\n\n'
        'data: {"event": "message.delta", "type": "interim_assistant",'
        ' "content": "Let me look."}\n\n'
        'data: {"event": "tool.progress", "type": "tool.started",'
        ' "name": "read", "arguments": {"path": "f"}, "tool_id": "t1"}\n\n'
        'data: {"event": "tool.progress", "type": "tool.completed",'
        ' "name": "read", "result": "", "duration_s": 0.1, "tool_id": "t1"}\n\n'
        'data: {"event": "message.delta", "type": "assistant.delta",'
        ' "text": "The file is empty."}\n\n'
        'data: {"event": "run.completed", "output": "The file is empty.",'
        ' "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}}\n\n'
        ": stream closed\n"
    )
    assert await _content_of(body) == "Let me look.\n\nThe file is empty."


async def test_a_truncated_stream_is_completed_by_the_terminal_output() -> None:
    """The deltas can stop early (a dropped chunk, a lost event). What arrived
    is a prefix of the answer, so only the missing tail is appended and the
    joined text equals the answer instead of doubling its opening."""
    body = _completed_body(deltas=["Hello "], output="Hello world")
    assert await _content_of(body) == "Hello world"


async def test_a_lagging_subscriber_is_told_its_trace_is_incomplete() -> None:
    """The run's broadcast buffer overflowed and the server dropped events.
    The turn can still finish, so this cannot be an error — but silence would
    make "we stopped being told" indistinguishable from "nothing happened"."""
    calls: list[httpx.Request] = []
    body = (
        'data: {"event": "lagged", "missed": 7}\n\n'
        'data: {"event": "run.completed", "output": "done"}\n\n'
        ": stream closed\n"
    )
    backend = _mock_backend(calls, sse_body=body)
    events = [event async for event in backend.run(AgentLoopRequest(prompt="hi", session_id="s1"))]
    notes = [event.text for event in events if event.kind == "progress"]
    assert any("7" in note for note in notes)


async def test_an_error_frame_surfaces_instead_of_being_dropped() -> None:
    """The channel declares ``event: error`` with a ``message`` payload."""
    calls: list[httpx.Request] = []
    body = (
        'data: {"event": "error", "message": "provider exploded"}\n\n'
        'data: {"event": "run.failed", "error": "run failed"}\n\n'
        ": stream closed\n"
    )
    backend = _mock_backend(calls, sse_body=body)
    events = [event async for event in backend.run(AgentLoopRequest(prompt="hi", session_id="s1"))]
    errors = [event.text for event in events if event.kind == "error"]
    assert "provider exploded" in errors


# ── stopping a turn must stop the remote run too ────────────────────────────


async def test_closing_the_turn_stops_the_remote_run() -> None:
    """The run lives on the server, not in this connection. Cancelling the turn
    (the user pressing stop) tears down the subscription, which the Gateway
    does *not* treat as a stop — without an explicit call the agent keeps
    working and spending on a turn nobody is waiting for."""
    calls: list[httpx.Request] = []
    backend = _mock_backend(calls, sse_body=SSE_BODY)

    stream = backend.run(AgentLoopRequest(prompt="hi", session_id="s1"))
    # Consume the opening frame so the run is registered, then abandon it the
    # way a cancelled turn does.
    await stream.__anext__()
    await stream.aclose()

    stop = [call for call in calls if call.url.path.endswith("/stop")]
    assert stop, "the remote run was left running"
    assert stop[0].url.path == "/v1/runs/run_1/stop"
    assert stop[0].method == "POST"


async def test_a_completed_turn_does_not_stop_its_own_run() -> None:
    """A terminal event means the server already finished; a stray /stop would
    be noise against a closed run."""
    calls: list[httpx.Request] = []
    backend = _mock_backend(calls, sse_body=SSE_BODY)
    [event async for event in backend.run(AgentLoopRequest(prompt="hi", session_id="s1"))]
    assert not [call for call in calls if call.url.path.endswith("/stop")]
