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
    ``clarify_request`` and its answer must go back to the run's clarify
    endpoint — the run-scoped route the community ``api_server`` actually
    registers, not the ``chat/completions/{session_id}`` one only another
    build exposes (which 404s here)."""
    body = SSE_BODY.replace(
        'data: {"event": "approval.request", "tool_name": "shell",'
        ' "arguments": "rm -rf build", "choices": ["once", "deny"]}\n'
        "\n",
        'data: {"event": "clarify.request", "run_id": "run_1", "clarify_id": "c-1",'
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
    # Run-scoped, matching where the approval goes.
    assert answer.url.path == "/v1/runs/run_1/clarify"
    # The field is ``response``: that is the name the endpoint reads, and
    # ``answer`` would be rejected as missing.
    assert json.loads(answer.content) == {"clarify_id": "c-1", "response": "postgres"}


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
    conversation content to the log file.

    A stream that was *entirely* unreadable is also a contract mismatch, so
    the turn fails rather than completing empty: that is the difference
    between "the backend had nothing to say" and "we could not read what it
    said", and only the second is the operator's problem to fix.
    """
    from kagweb.services.agent_loop.protocol import AgentLoopError

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
        with pytest.raises(AgentLoopError):
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


# ---------------------------------------------------------------------------
# A misconfigured endpoint must fail loudly, not quietly
# ---------------------------------------------------------------------------


def _openai_sse_body() -> str:
    """What an OpenAI ``/v1/chat/completions`` stream actually looks like."""
    return (
        'data: {"id":"c1","object":"chat.completion.chunk","created":1,"model":"m",'
        '"choices":[{"index":0,"delta":{"role":"assistant"},"finish_reason":null}]}\n'
        "\n"
        'data: {"id":"c1","object":"chat.completion.chunk","created":1,"model":"m",'
        '"choices":[{"index":0,"delta":{"content":"Hello"},"finish_reason":null}]}\n'
        "\n"
        'data: {"id":"c1","object":"chat.completion.chunk","created":1,"model":"m",'
        '"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n'
        "\n"
    )


async def test_openai_chat_stream_is_rejected_with_an_actionable_error() -> None:
    """Pointing the profile at chat/completions must not silently yield nothing.

    That endpoint answers 200 and streams, so the only symptom is an empty
    turn. It carries no reasoning channel, no tool arguments or results, and
    nothing to answer an approval, a clarification or a stop — so the turn is
    failed with a message naming the fix, instead of completing empty.
    """
    from kagweb.services.agent_loop.protocol import AgentLoopError

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/v1/runs") and request.method == "POST":
            return httpx.Response(202, json={"run_id": "run_1"})
        if path.endswith("/events"):
            return httpx.Response(
                200,
                text=_openai_sse_body(),
                headers={"content-type": "text/event-stream"},
            )
        return httpx.Response(404, json={"error": "not found"})

    backend = RunsAgentLoopBackend(
        name="intellect-runs",
        url="http://gateway.test",
        turn_path="/v1/runs",
        api_key="k",
        headers={},
        timeout_seconds=30,
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(AgentLoopError) as excinfo:
        async for _ in backend.run(AgentLoopRequest(prompt="hi")):
            pass

    message = str(excinfo.value)
    # Names the wrong endpoint and the right one.
    assert "chat-completions" in message
    assert "intellect-runs" in message


async def test_readable_stream_with_no_events_is_not_rejected() -> None:
    """A run that legitimately carries only lifecycle frames still completes.

    The guard is about frames we could *not read*, not about a quiet stream —
    failing the latter would break a correct backend. Every frame here is
    recognized (a heartbeat, then completed-with-empty-output), so the turn
    ends with no answer text; the capability layer's own empty-answer note
    covers that, and it points at the model rather than at a contract error.
    """
    body = (
        'data: {"event": "run.started", "run_id": "run_1"}\n'
        "\n"
        'data: {"event": "message.delta", "type": "thinking.progress",'
        ' "elapsed_s": 2.0, "silent_s": 1.0}\n'
        "\n"
        'data: {"event": "run.completed", "output": ""}\n'
        "\n"
    )
    calls: list[httpx.Request] = []
    backend = _mock_backend(calls, sse_body=body)

    events = [event async for event in backend.run(AgentLoopRequest(prompt="hi"))]

    # No contract error: it finished and simply had no answer text.
    assert not any(event.kind == "error" for event in events)


async def test_unreadable_frames_without_the_openai_marker_also_fail() -> None:
    """A vendor that omits the ``object`` marker is still caught.

    Detection is by payload shape, not by one field name, so an unrecognized
    stream that streams *something* cannot pass as an empty answer either.
    """
    from kagweb.services.agent_loop.protocol import AgentLoopError

    body = (
        'data: {"something":"we have never seen","detached":true}\n'
        "\n"
        'data: {"another":"unmapped frame"}\n'
        "\n"
    )
    calls: list[httpx.Request] = []
    backend = _mock_backend(calls, sse_body=body)

    with pytest.raises(AgentLoopError) as excinfo:
        async for _ in backend.run(AgentLoopRequest(prompt="hi")):
            pass

    assert "none could be read" in str(excinfo.value)


async def test_sse_event_name_is_used_when_the_payload_names_no_kind() -> None:
    """The ``event:`` line is the last-resort type marker.

    Some vendors put the only kind on the SSE line and send a body that names
    none of its own. Dropping that line is why such frames used to look
    unmappable; carrying it into the payload lets them translate.
    """
    body = (
        'event: run.completed\ndata: {"run_id": "run_1", "output": "answer via the event line"}\n\n'
    )
    calls: list[httpx.Request] = []
    backend = _mock_backend(calls, sse_body=body)

    events = [event async for event in backend.run(AgentLoopRequest(prompt="hi"))]

    assert any(
        event.kind == "content" and event.text == "answer via the event line" for event in events
    )


async def test_payload_own_kind_wins_over_the_sse_event_name() -> None:
    """A payload that names its own kind keeps it; the line is only a fallback.

    This is the shape the authoritative Rust server emits for text: the SSE
    line says ``message.delta`` while the payload's ``type`` says
    ``assistant.delta``. Letting the line override would lose the distinction
    the payload is carrying.
    """
    body = (
        'event: message.delta\ndata: {"type": "reasoning.delta", "text": "thinking out loud"}\n\n'
    )
    calls: list[httpx.Request] = []
    backend = _mock_backend(calls, sse_body=body)

    events = [event async for event in backend.run(AgentLoopRequest(prompt="hi"))]

    assert any(event.kind == "thinking" and event.text == "thinking out loud" for event in events)


async def test_a_truncated_run_is_not_reported_as_a_clean_finish() -> None:
    """``run.completed`` with ``completed: false`` means the text is a prefix.

    The channel used to drop these fields entirely, so a truncated run looked
    exactly like a finished one and the partial answer was presented as final.
    """
    body = (
        'data: {"event": "message.delta", "type": "assistant.delta", "text": "partial "}\n'
        "\n"
        'data: {"event": "run.completed", "output": "partial answer",'
        ' "completed": false, "partial": true,'
        ' "error": "Response truncated due to output length limit"}\n'
        "\n"
    )
    calls: list[httpx.Request] = []
    backend = _mock_backend(calls, sse_body=body)

    events = [event async for event in backend.run(AgentLoopRequest(prompt="hi"))]

    # The text still arrives — it is worth keeping…
    assert any(e.kind == "content" and "partial answer" in e.text for e in events)
    # …but the reader is told it stopped short, with a machine-readable reason
    # in the same vocabulary the ACP family uses.
    error = next(e for e in events if e.kind == "error")
    assert error.data.get("stop_reason") == "max_tokens"
    assert "stopped before finishing" in error.text


async def test_a_clean_run_carries_no_incomplete_marker() -> None:
    """The fields are absent on older services; that must stay a clean finish."""
    body = (
        'data: {"event": "message.delta", "type": "assistant.delta", "text": "all done"}\n'
        "\n"
        'data: {"event": "run.completed", "output": "all done"}\n'
        "\n"
    )
    calls: list[httpx.Request] = []
    backend = _mock_backend(calls, sse_body=body)

    events = [event async for event in backend.run(AgentLoopRequest(prompt="hi"))]

    assert not any(e.kind == "error" for e in events)
    assert any(e.kind == "content" and "all done" in e.text for e in events)


async def test_an_explicitly_complete_run_carries_no_marker() -> None:
    body = (
        'data: {"event": "run.completed", "output": "done",'
        ' "completed": true, "partial": false}\n'
        "\n"
    )
    calls: list[httpx.Request] = []
    backend = _mock_backend(calls, sse_body=body)

    events = [event async for event in backend.run(AgentLoopRequest(prompt="hi"))]

    assert not any(e.kind == "error" for e in events)
