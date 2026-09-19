"""AcpAgentLoopBackend: event mapping, approval parking, session lifecycle."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys

import pytest

from kagweb.services.agent_loop.acp_backend import AcpAgentLoopBackend
from kagweb.services.agent_loop.protocol import AgentLoopRequest

# Every scenario here spawns the fake ACP agent, which speaks the wire
# dialect through the SDK itself (`kagweb[acp]`). Skip the whole module when
# the extra is not installed — same hygiene as the pty/termios gate in
# test_chat_terminal.py; CI installs the extra and keeps the coverage.
pytest.importorskip("acp")

pytestmark = pytest.mark.asyncio

_FAKE_AGENT = Path(__file__).parent / "fake_acp_agent.py"


@pytest.fixture(autouse=True)
def _isolated_session_store(tmp_path, monkeypatch) -> None:
    """Keep durable agent-session records out of the developer's real tree.

    The store resolves its directory through the process-global
    ``PathService``, which in a source checkout points at the live
    ``data/user``; without this, every test would leave records beside the
    running instance's own.
    """
    from kagweb.services.agent_loop import acp_session_store
    from kagweb.services.path_service import PathService

    path_service = PathService(workspace_root=tmp_path)
    monkeypatch.setattr(
        acp_session_store,
        "_store_dir",
        lambda: path_service.get_user_root() / "runtime" / acp_session_store._STORE_DIRNAME,
    )


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


async def test_approval_label_stays_short_while_detail_keeps_full_text(tmp_path) -> None:
    """A verbose ACP title must not become the approval card's "run X" label.

    Intellect sends the whole scan report plus the command as ``title``
    (``acp_adapter/permissions.py``), so reading ``title`` straight into
    ``name`` produced a thousand-character "tool name" that the card printed
    twice — once repr()-escaped, once raw.
    """
    result_file = tmp_path / "result.json"
    backend = _backend("long-approval", result_file)

    events = await _consume_until_approval(backend, _request("acp-long", tmp_path), "once")

    approval = next(event for event in events if event.kind == "approval_request")
    # The label is the command from raw_input, first line only, clamped short.
    assert approval.name.startswith("cd /tmp && for q in a b;")
    assert len(approval.name) <= 80
    assert "\n" not in approval.name
    # The detail still carries the report and the command for the reader.
    assert "Security scan" in approval.text
    assert "\n" in approval.text
    # One rendering of the report, not the title's copy plus the content's.
    assert approval.text.count("Security scan — [HIGH]") == 1


async def test_tool_call_preview_prefers_content_over_title(tmp_path) -> None:
    """Detail picks one source; the title's near-duplicate is not repeated.

    ContentToolCallContent nests the text block under ``.content``; the
    preview must reach it (reading ``.text`` off the wrapper yields nothing)
    and must not then also append the title, which carries the same words.
    """
    from acp import schema

    from kagweb.services.agent_loop.acp_backend import _tool_call_preview

    tool_call = schema.ToolCallUpdate(
        tool_call_id="t1",
        title="scan: run the thing",
        content=[
            schema.ContentToolCallContent(
                type="content",
                content=schema.TextContentBlock(type="text", text="scan\n$ run the thing"),
            )
        ],
        raw_input={"command": "run the thing"},
    )
    assert _tool_call_preview(tool_call) == "scan\n$ run the thing"


async def test_tool_call_preview_falls_back_so_nothing_is_hidden(tmp_path) -> None:
    """An approval must disclose what it will run, so detail is never empty.

    A backend may send only ``raw_input``. The label above the body is
    clamped, so without this fallback the user would approve a command whose
    tail they never saw.
    """
    from acp import schema

    from kagweb.services.agent_loop.acp_backend import _tool_call_preview

    command = "echo padding padding padding padding padding && rm -rf /data"
    only_input = schema.ToolCallUpdate(
        tool_call_id="t1", kind="execute", raw_input={"command": command}
    )
    assert _tool_call_preview(only_input) == command

    titled = schema.ToolCallUpdate(tool_call_id="t2", title="Shell", raw_input={})
    assert _tool_call_preview(titled) == "Shell"


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


# ---------------------------------------------------------------------------
# Stop reasons: an early stop must not read as a clean finish
# ---------------------------------------------------------------------------


async def test_truncated_stop_reason_surfaces_an_error(tmp_path) -> None:
    """``max_tokens`` means the reply is a prefix, not an answer.

    Intellect returns ``completed: False`` with an error when the model hits
    the output ceiling; the wire only carries a stop_reason, so the backend
    has to react to that or a half-finished reply reaches the reader labelled
    "completed".
    """
    backend = _backend("full-stop-max_tokens", tmp_path / "result.json")

    events = [event async for event in backend.run(_request("acp-max", tmp_path))]

    assert [event.text for event in events if event.kind == "content"] == ["Hello world"]
    error = next(event for event in events if event.kind == "error")
    assert "output length limit" in error.text
    assert error.data.get("stop_reason") == "max_tokens"


async def test_clean_end_turn_surfaces_no_error(tmp_path) -> None:
    backend = _backend("full-stop-end_turn", tmp_path / "result.json")

    events = [event async for event in backend.run(_request("acp-ok", tmp_path))]

    assert not [event for event in events if event.kind == "error"]


def test_stop_reason_mapping_covers_the_protocol_vocabulary() -> None:
    """Only ``end_turn`` is a clean finish; every other value reports.

    The ACP SDK pins ``stop_reason`` to a literal set, so a vendor value
    cannot reach the mapping today — the open-ended default is what keeps a
    *future* vocabulary from silently reading as success, which is the exact
    failure this guards. ``cancelled`` is handled by the caller before this
    point, so it is not special-cased here.
    """
    from kagweb.services.agent_loop.acp_backend import _incomplete_stop_reason

    assert _incomplete_stop_reason("end_turn") == ""
    for reason in ("max_tokens", "max_turn_requests", "refusal"):
        assert _incomplete_stop_reason(reason), reason
    assert _incomplete_stop_reason("some_future_reason")


# ---------------------------------------------------------------------------
# Durable agent session: survives a KAGWeb restart
# ---------------------------------------------------------------------------


async def test_agent_session_is_recorded_and_reused_across_managers(tmp_path) -> None:
    """A restarted process must re-attach, not start a fresh agent session.

    The agent keeps the conversation; KAGWeb only holds an opaque id. It used
    to live in memory alone, so every KAGWeb restart silently gave the agent
    amnesia (``history=0`` on the next turn). The fake agent records whether
    it was asked to ``load_session``, which is what proves the re-attach.

    ``plain`` (no approval) keeps the turn single-shot: a scenario that parks
    on a permission would hang here, since nothing answers it.
    """
    from kagweb.services.agent_loop import acp_session_store

    result_file = tmp_path / "result.json"
    session_key = "acp-durable"

    backend = _backend("plain", result_file)
    [event async for event in backend.run(_request(session_key, tmp_path))]
    first_id = backend._manager._handles[session_key].acp_session_id
    assert first_id
    stored = acp_session_store.load_acp_session(session_key)
    assert stored is not None and stored[0] == first_id
    await backend._manager.close_all()

    # Simulate a KAGWeb restart: a fresh backend, no in-memory handles.
    result_file.unlink()
    restarted = _backend("plain", result_file)
    assert restarted._manager._handles == {}

    [event async for event in restarted.run(_request(session_key, tmp_path))]

    assert restarted._manager._handles[session_key].acp_session_id == first_id
    assert json.loads(result_file.read_text())["reused_session"] is True

    await restarted._manager.close_all()


async def test_stale_session_record_falls_back_to_a_new_session(tmp_path) -> None:
    """An id the agent no longer knows must not break the turn.

    Agents drop sessions (idle expiry, a wiped state DB); the re-attach has to
    degrade to ``new_session`` rather than failing every subsequent turn.
    """
    from kagweb.services.agent_loop import acp_session_store

    session_key = "acp-stale"
    acp_session_store.save_acp_session(session_key, session_id="does-not-exist-in-the-agent")

    backend = _backend("plain-reject-load", tmp_path / "result.json")
    events = [event async for event in backend.run(_request(session_key, tmp_path))]

    assert [event.text for event in events if event.kind == "content"] == ["Hello world"]
    # A fresh id replaced the stale one, and it was recorded for next time.
    fresh_id = backend._manager._handles[session_key].acp_session_id
    assert fresh_id and fresh_id != "does-not-exist-in-the-agent"
    stored = acp_session_store.load_acp_session(session_key)
    assert stored is not None and stored[0] == fresh_id

    await backend._manager.close_all()


async def test_vanished_workspace_cwd_does_not_wedge_the_session(tmp_path) -> None:
    """A recorded cwd that no longer exists must not fail every later turn.

    The record stores an absolute path from the session's own workspace.
    That directory can be reclaimed or the workspace root moved while the
    record survives; spawning into a missing directory raises before the
    handshake, and because the record is only written after a *successful*
    spawn it would never be replaced. The session would be permanently dead
    (spawn accepted the path, then failed at ``__aenter__`` — so this has to
    be caught at record-read time).
    """
    from kagweb.services.agent_loop import acp_session_store

    session_key = "acp-vanished"
    gone = tmp_path / "reclaimed-workspace"
    acp_session_store.save_acp_session(session_key, session_id="agent-in-a-gone-dir", cwd=str(gone))
    assert not gone.exists()

    backend = _backend("plain", tmp_path / "result.json")
    events = [event async for event in backend.run(_request(session_key, tmp_path))]
    assert [event.text for event in events if event.kind == "content"] == ["Hello world"]

    handle = backend._manager._handles[session_key]
    # A fresh session in the caller's directory, not the vanished one.
    assert handle.acp_session_id != "agent-in-a-gone-dir"
    assert handle.cwd != str(gone)
    await backend._manager.close_all()


def test_store_ignores_records_from_another_version(tmp_path) -> None:
    """A record shape this build cannot read is treated as absent.

    ``_VERSION`` is only worth storing if reads honor it; otherwise a future
    shape change would be read as if it were the current one.
    """
    import json

    from kagweb.services.agent_loop import acp_session_store

    session_key = "acp-other-version"
    acp_session_store.save_acp_session(session_key, session_id="agent-v1")
    path = acp_session_store._entry_path(session_key)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["version"] = 999
    path.write_text(json.dumps(payload), encoding="utf-8")

    assert acp_session_store.load_acp_session(session_key) is None


# ---------------------------------------------------------------------------
# Probe readiness: only an interactive setup method means "not set up"
# ---------------------------------------------------------------------------


def _auth_methods(*types: str):
    """Build advertised auth methods by kind."""
    from acp import schema

    built = []
    for index, kind in enumerate(types):
        if kind == "terminal":
            built.append(
                schema.TerminalAuthMethod(
                    id=f"setup-{index}", name="Configure Intellect provider", type="terminal"
                )
            )
        elif kind == "agent":
            built.append(schema.AuthMethodAgent(id=f"prov-{index}", name="runtime credentials"))
        else:
            built.append(
                schema.EnvVarAuthMethod(id=f"var-{index}", name="API key", vars=[], type="env_var")
            )
    return built


def test_a_ready_install_is_not_reported_as_missing_credentials() -> None:
    """A configured agent advertises a usable method *alongside* the setup one.

    The registry requires at least one method and Intellect always appends its
    terminal setup errand, so "any method at all" is true even when everything
    is configured — which is how a working install got told to run `login`
    again.
    """
    from kagweb.services.agent_loop.acp_backend import _auth_needs_setup

    assert _auth_needs_setup(_auth_methods("agent", "terminal")) is False
    assert _auth_needs_setup(_auth_methods("env_var")) is False
    assert _auth_needs_setup(_auth_methods("agent")) is False


def test_only_a_setup_method_means_setup_is_needed() -> None:
    from kagweb.services.agent_loop.acp_backend import _auth_needs_setup

    assert _auth_needs_setup(_auth_methods("terminal")) is True
    assert _auth_needs_setup(_auth_methods("terminal", "terminal")) is True


def test_an_agent_asking_for_no_auth_is_not_treated_as_unconfigured() -> None:
    """No methods at all is "nothing to do", not "credentials missing"."""
    from kagweb.services.agent_loop.acp_backend import _auth_needs_setup

    assert _auth_needs_setup([]) is False
    assert _auth_needs_setup(None) is False


async def test_probe_reports_a_missing_credential_only_for_a_setup_only_agent(
    tmp_path,
) -> None:
    """End to end through ``probe()`` with the real handshake.

    The fake agent advertises nothing, which is the "no auth wanted" case, so
    the detail must be the plain handshake line with no login advice.
    """
    backend = _backend("plain", tmp_path / "result.json")

    ok, detail = await backend.probe()

    assert ok is True
    assert "handshake ok" in detail
    assert "no provider credentials" not in detail
    assert "login" not in detail


# ---------------------------------------------------------------------------
# Clarify: ACP delivers it as a form elicitation, not a permission
# ---------------------------------------------------------------------------


async def _consume_until_clarify(backend, request, answer: str):
    """Drive one turn, answering the first clarify with *answer*."""
    events = []
    async for event in backend.run(request):
        events.append(event)
        if event.kind == "clarify_request":
            await backend.respond_clarify(event.data["request_id"], answer)
    return events


async def test_clarify_surfaces_as_a_card_and_the_answer_reaches_the_agent(tmp_path) -> None:
    """The whole point of phase 2: a question must reach the user, and back.

    Without a client-side ``create_elicitation`` the SDK rejects the request
    outright (the route is not optional), so the agent's question would fail
    the turn instead of being shown.
    """
    result_file = tmp_path / "result.json"
    backend = _backend("plain-elicitation", result_file)

    events = await _consume_until_clarify(backend, _request("acp-clarify", tmp_path), "sqlite")

    clarify = next(event for event in events if event.kind == "clarify_request")
    # The card carries the question and the suggested choices.
    assert clarify.text == "Which database should I target?"
    assert clarify.data["choices"] == ["postgres", "sqlite"]
    assert clarify.data["request_id"].startswith("acp-clarify-")

    # …and the answer comes back as an *accepted* elicitation carrying it.
    recorded = json.loads(result_file.read_text())
    assert recorded["elicitation_action"] == "accept"
    assert recorded["elicitation_answer"] == "sqlite"

    await backend._manager.close_all()


async def test_a_skipped_clarify_is_declined_not_answered(tmp_path) -> None:
    """An empty answer must be a decline.

    The turn can be swept (cancel, timeout) while a question is parked; the
    agent must learn "no answer" rather than receive an empty string as if it
    were the user's reply.
    """
    result_file = tmp_path / "result.json"
    backend = _backend("plain-elicitation", result_file)

    events = await _consume_until_clarify(backend, _request("acp-clarify-skip", tmp_path), "")

    assert any(event.kind == "clarify_request" for event in events)
    recorded = json.loads(result_file.read_text())
    assert recorded["elicitation_action"] == "decline"
    assert recorded["elicitation_answer"] == ""

    await backend._manager.close_all()


def test_a_swept_turn_declines_a_parked_clarify() -> None:
    """Cancelling must not answer a question with the approval word.

    ``deny_all_pending`` is the cancel sweep, and it used to resolve every
    parked future with ``"deny"`` — which for a clarify would be handed to the
    agent as the user's literal answer.
    """
    import asyncio as _asyncio

    from kagweb.services.agent_loop.acp_backend import _AcpClientHandler

    handler = _AcpClientHandler()
    approval = _asyncio.get_event_loop_policy().new_event_loop().create_future()
    clarify = _asyncio.get_event_loop_policy().new_event_loop().create_future()
    handler._pending = {"acp-approval-1": approval, "acp-clarify-1": clarify}
    handler._pending_kind = {"acp-approval-1": "approval", "acp-clarify-1": "clarify"}

    handler.deny_all_pending()

    assert approval.result() == "deny"
    assert clarify.result() == ""


async def test_a_url_elicitation_is_declined_rather_than_left_hanging() -> None:
    """A mode with nothing to render must not stall the agent.

    URL mode is an out-of-band link; the card cannot present it, and leaving
    the request unanswered would park the agent until its own timeout.
    """
    from acp import schema as _schema

    from kagweb.services.agent_loop.acp_backend import _AcpClientHandler

    handler = _AcpClientHandler()
    url_mode = _schema.ElicitationUrlSessionMode(
        session_id="s", elicitation_id="e1", url="https://example.test/login"
    )

    response = await handler.create_elicitation(message="Sign in", mode=url_mode)

    assert getattr(response, "action", "") == "decline"


def test_a_future_registered_without_a_kind_is_still_swept() -> None:
    """The sweep must key off ``_pending``, the parked-future set itself.

    Kind is bookkeeping for *which word* answers a request, not the register
    of what is parked. Iterating the kind map instead would silently skip a
    future whose kind was never recorded, leaving it to hang until the agent's
    own timeout.
    """
    import asyncio as _asyncio

    from kagweb.services.agent_loop.acp_backend import _AcpClientHandler

    handler = _AcpClientHandler()
    loop = _asyncio.new_event_loop()
    try:
        orphan = loop.create_future()
        # Registered as pending, but with no kind entry — the case a
        # second map invites.
        handler._pending = {"acp-approval-9": orphan}

        handler.deny_all_pending()

        # Swept, and swept as an approval (the original behaviour).
        assert orphan.result() == "deny"
        assert handler._pending == {}
    finally:
        loop.close()


def test_a_clarify_registered_without_a_kind_is_left_unanswered() -> None:
    """A kind-less future sweeps as an approval, so it must not be a clarify.

    This pins the *default*: whatever is registered must reach a resolved
    state. Which word it gets follows the recorded kind when there is one.
    """
    import asyncio as _asyncio

    from kagweb.services.agent_loop.acp_backend import _AcpClientHandler

    handler = _AcpClientHandler()
    loop = _asyncio.new_event_loop()
    try:
        clarify = loop.create_future()
        handler._pending = {"acp-clarify-9": clarify}
        handler._pending_kind = {"acp-clarify-9": "clarify"}

        handler.deny_all_pending()

        # Empty, i.e. the handler turns it into a decline rather than handing
        # the agent the word "deny" as the user's answer.
        assert clarify.result() == ""
    finally:
        loop.close()


def test_clarify_input_from_the_agent_is_bounded() -> None:
    """The question and the choices come from the agent's schema and are
    persisted with the turn, so a malformed form must not write megabytes."""
    from acp import schema

    from kagweb.services.agent_loop.acp_backend import (
        _MAX_CLARIFY_CHOICE_CHARS,
        _MAX_CLARIFY_CHOICES,
        _MAX_CLARIFY_QUESTION_CHARS,
        _elicitation_question,
    )

    form = schema.ElicitationSchema(
        type="object",
        properties={
            "answer": schema.ElicitationStringPropertySchema(
                type="string",
                title="Q" * 9000,
                enum=[f"choice-{i}-{'x' * 500}" for i in range(500)],
            )
        },
        required=["answer"],
    )

    question, choices = _elicitation_question("m" * 9000, form)

    assert len(question) == _MAX_CLARIFY_QUESTION_CHARS
    assert len(choices) == _MAX_CLARIFY_CHOICES
    assert max(len(choice) for choice in choices) == _MAX_CLARIFY_CHOICE_CHARS


def test_a_real_elicitation_is_not_truncated_by_the_caps() -> None:
    """The caps are a backstop, not a formatting choice — real input passes."""
    from acp import schema

    from kagweb.services.agent_loop.acp_backend import _elicitation_question

    form = schema.ElicitationSchema(
        type="object",
        properties={
            "answer": schema.ElicitationStringPropertySchema(
                type="string",
                title="Which database should I target?",
                enum=["postgres", "sqlite"],
            )
        },
        required=["answer"],
    )

    question, choices = _elicitation_question("The agent needs more information.", form)

    assert question == "Which database should I target?"
    assert choices == ["postgres", "sqlite"]


async def test_a_request_left_parked_by_the_previous_turn_is_swept(tmp_path) -> None:
    """A request that lands after a turn's sweep must not stay unresolved.

    The sweep runs while the old sink is still attached, so there is a window
    in which the agent's request is registered but nobody will answer it. The
    next turn clears it; otherwise it sits in ``_pending`` — an unresolved
    future the agent is blocked on — until the agent's own timeout.

    Asserts the *ordering*, not just the end state: the turn's own tail sweep
    would also resolve it eventually, so only "swept before the prompt is
    issued" shows the leftover was cleared rather than merely outlived.
    """
    result_file = tmp_path / "result.json"
    backend = _backend("plain", result_file)
    request = _request("acp-leftover", tmp_path)
    [event async for event in backend.run(request)]

    handle = backend._manager._handles["acp-leftover"]
    loop = asyncio.get_running_loop()
    leftover = loop.create_future()
    handle.client._pending["acp-clarify-stale"] = leftover
    handle.client._pending_kind["acp-clarify-stale"] = "clarify"

    order: list[str] = []
    real_sweep = handle.client.deny_all_pending
    real_prompt = handle.connection.prompt

    def spy_sweep() -> None:
        order.append("sweep")
        real_sweep()

    async def spy_prompt(*args, **kwargs):
        order.append("prompt")
        return await real_prompt(*args, **kwargs)

    handle.client.deny_all_pending = spy_sweep
    handle.connection.prompt = spy_prompt

    [event async for event in backend.run(request)]

    # Cleared before the agent was asked anything…
    assert "prompt" in order and "sweep" in order
    assert order.index("sweep") < order.index("prompt")
    assert leftover.done()
    assert leftover.result() == ""
    assert "acp-clarify-stale" not in handle.client._pending

    await backend._manager.close_all()


# ---------------------------------------------------------------------------
# Per-turn model selection: the advertised selector (design §3.1 / §4.2-3)
# ---------------------------------------------------------------------------


async def test_acp_lists_advertised_model_options_via_probe(tmp_path) -> None:
    """The composer's option list comes from the agent's own handshake answer."""
    from kagweb.services.agent_loop.acp_backend import _MODEL_OPTIONS_CACHE

    backend = _backend("models", tmp_path / "result.json")
    options = await asyncio.wait_for(backend.list_model_options(""), timeout=20)

    assert [row["id"] for row in options] == [
        "deepseek:deepseek-flash",
        "deepseek:deepseek-v4-pro",
    ]
    assert options[0]["is_current"] is True
    assert options[1]["is_current"] is False
    assert options[1]["description"] == "Bigger context"
    # the throwaway probe child was closed, not left running
    assert not backend._manager._handles
    _MODEL_OPTIONS_CACHE.clear()


async def test_acp_agent_without_selector_lists_nothing(tmp_path) -> None:
    """No advertised option is a valid shape — the caller falls back to the
    profile's curated list."""
    from kagweb.services.agent_loop.acp_backend import _MODEL_OPTIONS_CACHE

    backend = _backend("full", tmp_path / "result.json")
    options = await asyncio.wait_for(backend.list_model_options(""), timeout=20)

    assert options is None
    # …and the "no selector" answer is cached like a successful probe, so the
    # composer's repeated fetches do not spawn a child each time.
    assert backend._config_key in _MODEL_OPTIONS_CACHE
    _MODEL_OPTIONS_CACHE.clear()


async def test_acp_applies_per_turn_model_via_config_option(tmp_path) -> None:
    """A per-turn model rides session/set_config_option, before the prompt.

    The agent's refreshed selector comes back in the response and is kept on
    the handle, so the composer shows the session's real current model.
    """
    result_file = tmp_path / "result.json"
    backend = _backend("models-stop-end_turn", result_file)
    request = _request("acp-models", tmp_path)
    request = AgentLoopRequest(
        prompt="hi",
        session_id="acp-models",
        workdir=str(tmp_path),
        model="deepseek:deepseek-v4-pro",
    )

    events = [event async for event in backend.run(request)]

    assert not [event for event in events if event.kind == "error"]
    outcome = json.loads(result_file.read_text())
    assert outcome["applied_model"] == "deepseek:deepseek-v4-pro"
    handle = backend._manager._handles.get("acp-models")
    assert handle is not None and handle.model_options is not None
    current = [row for row in handle.model_options if row["is_current"]]
    assert [row["id"] for row in current] == ["deepseek:deepseek-v4-pro"]
    await backend._manager.close_all()


async def test_acp_turn_without_model_leaves_the_agent_default(tmp_path) -> None:
    result_file = tmp_path / "result.json"
    backend = _backend("models-stop-end_turn", result_file)
    request = AgentLoopRequest(
        prompt="hi", session_id="acp-nomodel", workdir=str(tmp_path), model=""
    )

    [event async for event in backend.run(request)]

    outcome = json.loads(result_file.read_text())
    assert outcome["applied_model"] == ""
    await backend._manager.close_all()


async def test_acp_filter_turn_model_uses_the_advertised_selector(tmp_path) -> None:
    """With a live session the whitelist is the advertised option ids."""
    backend = _backend("models", tmp_path / "result.json")
    await asyncio.wait_for(backend._manager.ensure("acp-filter", str(tmp_path)), timeout=20)

    assert backend.filter_turn_model("deepseek:deepseek-v4-pro", "acp-filter") == (
        "deepseek:deepseek-v4-pro"
    )
    # A stale pick (operator/agent vocab changed) degrades to "backend default".
    assert backend.filter_turn_model("ghost-model", "acp-filter") == ""
    assert backend.filter_turn_model("", "acp-filter") == ""
    await backend._manager.close_all()


async def test_acp_filter_without_a_session_passes_through(tmp_path) -> None:
    """No advertised selector available: fail-soft, like the apply step."""
    backend = _backend("models", tmp_path / "result.json")

    assert backend.filter_turn_model("anything", "no-such-session") == "anything"
