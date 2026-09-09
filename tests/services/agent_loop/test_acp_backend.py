"""AcpAgentLoopBackend: event mapping, approval parking, session lifecycle."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys

import pytest

from kagweb.services.agent_loop.acp_backend import AcpAgentLoopBackend
from kagweb.services.agent_loop.protocol import AgentLoopRequest

pytestmark = pytest.mark.asyncio

_FAKE_AGENT = Path(__file__).parent / "fake_acp_agent.py"


def _backend(scenario: str, result_file: Path) -> AcpAgentLoopBackend:
    return AcpAgentLoopBackend(
        name="fake-acp",
        command=sys.executable,
        base_args=["-u", str(_FAKE_AGENT), scenario],
        env={"FAKE_RESULT_FILE": str(result_file)},
        timeout_seconds=30,
    )


def _request(session_id: str, workdir: Path) -> AgentLoopRequest:
    return AgentLoopRequest(prompt="hi", session_id=session_id, workdir=str(workdir))


async def _consume_until_approval(backend: AcpAgentLoopBackend, request, decision: str):
    """Drive one turn, answering the first approval with *decision*."""
    events = []
    agen = backend.run(request)
    async for event in agen:
        events.append(event)
        if event.kind == "approval_request":
            await backend.respond_approval(event.data["request_id"], decision)
    return events


async def test_full_event_stream_and_approval_forwarding(tmp_path) -> None:
    result_file = tmp_path / "result.json"
    backend = _backend("full", result_file)

    events = await _consume_until_approval(backend, _request("acp-full", tmp_path), "once")

    kinds = [event.kind for event in events]
    # Deltas buffer into ONE content block, flushed at the tool boundary;
    # then the tool pair, the approval, and usage last.
    assert kinds == ["content", "tool_call", "tool_result", "approval_request", "usage"]
    contents = [event.text for event in events if event.kind == "content"]
    assert contents == ["Hello world"]
    call = events[1]
    result = events[2]
    assert call.name == "shell"
    assert result.text == "tool-out"
    assert result.data["is_error"] is False
    assert events[-1].data == {"input_tokens": 3, "output_tokens": 5, "total_tokens": 8}

    outcome = json.loads(result_file.read_text())
    # The decision came back as the allow_once option the agent offered.
    assert outcome["permission_option_id"] == "opt-allow"


async def test_deny_maps_to_reject_option(tmp_path) -> None:
    result_file = tmp_path / "result.json"
    backend = _backend("deny", result_file)

    await _consume_until_approval(backend, _request("acp-deny", tmp_path), "deny")

    outcome = json.loads(result_file.read_text())
    # deny maps to the ACP DeniedOutcome (no option id selected)
    assert outcome["permission_option_id"] == "outcome:DeniedOutcome"


async def test_cancelled_turn_preserves_child_for_next_turn(tmp_path) -> None:
    result_file = tmp_path / "result.json"
    backend = _backend("full", result_file)

    agen = backend.run(_request("acp-cancel", tmp_path))
    async for _ in agen:  # first event, then abandon the turn
        break
    await agen.aclose()

    handle = backend._manager._handles.get("acp-cancel")
    assert handle is not None and handle.alive  # child survives, session intact
    assert not handle.client._pending  # parked approvals were denied

    # The next turn on the same session reuses the same child.
    events = await _consume_until_approval(backend, _request("acp-cancel", tmp_path), "once")
    assert any(event.kind == "usage" for event in events)
    outcome = json.loads(result_file.read_text())
    assert outcome["permission_option_id"] == "opt-allow"


async def test_crashed_child_respawns_and_reloads_session(tmp_path) -> None:
    result_file = tmp_path / "result.json"
    backend = _backend("full", result_file)

    await _consume_until_approval(backend, _request("acp-crash", tmp_path), "once")
    handle = backend._manager._handles.get("acp-crash")
    first_process = handle.process
    handle.process.terminate()
    await asyncio.wait_for(handle.process.wait(), timeout=5)

    events = await _consume_until_approval(backend, _request("acp-crash", tmp_path), "once")
    assert any(event.kind == "usage" for event in events)
    new_handle = backend._manager._handles.get("acp-crash")
    assert new_handle.process is not first_process  # respawned
    assert json.loads(result_file.read_text())["reused_session"] is True


# ---------------------------------------------------------------------------
# Lifecycle hardening (review fixes): probe, children cap, shutdown sweep
# ---------------------------------------------------------------------------


async def test_probe_reports_ok_and_discards_its_child(tmp_path) -> None:
    backend = _backend("full", tmp_path / "result.json")
    ok, detail = await asyncio.wait_for(backend.probe(), timeout=20)
    assert ok is True
    assert "handshake ok" in detail
    # the throwaway probe child was closed, not left running
    assert not [h for h in backend._manager._handles.values() if h.key.startswith("probe-")]


async def test_manager_refuses_children_over_the_cap(tmp_path) -> None:
    from kagweb.services.agent_loop.acp_backend import AcpSessionManager
    from kagweb.services.agent_loop.protocol import AgentLoopError

    backend = _backend("full", tmp_path / "result.json")
    manager = AcpSessionManager("test-cap", max_children=1)
    manager.set_spawner(backend._spawn_session)

    await manager.ensure("cap-a", str(tmp_path))
    with pytest.raises(AgentLoopError):
        await manager.ensure("cap-b", str(tmp_path))
    await manager.close_all()


async def test_shutdown_all_terminates_children(tmp_path) -> None:
    from kagweb.services.agent_loop.acp_backend import shutdown_all_acp_sessions

    backend = _backend("full", tmp_path / "result.json")
    handle = await backend._manager.ensure("acp-shutdown", str(tmp_path))
    assert handle.alive

    await shutdown_all_acp_sessions()
    await asyncio.wait_for(handle.process.wait(), timeout=5)
    assert handle.process.returncode is not None
