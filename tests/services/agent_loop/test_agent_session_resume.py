"""The one-shot CLIs' native agent-session resume (history design L1/L2)."""

from __future__ import annotations

import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from kagweb.services.agent_loop import agent_session_store
from kagweb.services.agent_loop.builtin import PRESETS, resolve_transport
from kagweb.services.agent_loop.cli_backend import CliAgentLoopBackend, translate_codex
from kagweb.services.agent_loop.protocol import AgentLoopRequest


@pytest.fixture()
def isolated_store(tmp_path, monkeypatch):
    """Point the agent-session store at the test tree."""
    monkeypatch.setattr(
        agent_session_store,
        "_store_dir",
        lambda: tmp_path / "runtime" / "agent_sessions",
    )
    return tmp_path / "runtime" / "agent_sessions"


def _backend(resume_kind: str = "", **overrides: object) -> CliAgentLoopBackend:
    params: dict = {
        "name": "claude-code",
        "command": "claude",
        "base_args": ["-p", "--output-format", "stream-json", "--verbose"],
        "extra_args": [],
        "env": {},
        "timeout_seconds": 30,
        "translator": lambda obj, state: [],
        "resume_kind": resume_kind,
    }
    params.update(overrides)
    return CliAgentLoopBackend(**params)


def _request(session_id: str = "s1", content: str = "Q") -> AgentLoopRequest:
    return AgentLoopRequest(prompt=content, session_id=session_id)


def test_resume_kind_is_preset_truth() -> None:
    assert resolve_transport("claude-code").resume_kind == "claude"
    assert resolve_transport("codex").resume_kind == "codex"
    assert resolve_transport("opencode").resume_kind == "opencode"
    assert resolve_transport("hermes").resume_kind == ""
    assert PRESETS["claude-code"].resume_kind == "claude"


def test_store_roundtrip(isolated_store) -> None:
    assert agent_session_store.load_agent_session("claude-code:s1") is None
    agent_session_store.save_agent_session("claude-code:s1", agent_session_id="abc", cwd="/tmp/w")
    assert agent_session_store.load_agent_session("claude-code:s1") == "abc"
    agent_session_store.forget_agent_session("claude-code:s1")
    assert agent_session_store.load_agent_session("claude-code:s1") is None


def test_forget_agent_sessions_for_matches_keys(isolated_store) -> None:
    agent_session_store.save_agent_session("claude-code:s1", agent_session_id="a")
    agent_session_store.save_agent_session("codex:s1", agent_session_id="b")
    agent_session_store.save_agent_session("claude-code:s2", agent_session_id="c")
    removed = agent_session_store.forget_agent_sessions_for("s1")
    assert removed == 2
    assert agent_session_store.load_agent_session("claude-code:s2") == "c"


def test_claude_fresh_turn_dictates_a_session_id(isolated_store) -> None:
    backend = _backend("claude")
    request = _request()
    argv = backend.build_argv(request, fresh_session_id="11111111-1111-1111-1111-111111111111")
    # Flags injected before the prompt, prompt not folded with history.
    assert argv[-3:-1] == ["--session-id", "11111111-1111-1111-1111-111111111111"]
    assert argv[-1] == "Q"


def test_claude_resume_reuses_the_stored_session(isolated_store) -> None:
    agent_session_store.save_agent_session("claude-code:s1", agent_session_id="agent-9")
    backend = _backend("claude")
    argv = backend.build_argv(_request(), resume_id="agent-9")
    assert argv[argv.index("--resume") + 1] == "agent-9"
    assert argv[-1] == "Q"
    # L1: resuming means the agent owns the history — no folded transcript.
    assert "Conversation so far" not in argv[-1]


def test_claude_resume_folds_history_when_absent(isolated_store) -> None:
    """Without a stored session the turn is a fresh claude conversation: the
    folded transcript is then the only history channel."""
    backend = _backend("claude")
    request = AgentLoopRequest(
        prompt="Q",
        session_id="s1",
        history=[{"role": "user", "content": "earlier"}],
    )
    argv = backend.build_argv(request, fresh_session_id="u1")
    assert "Conversation so far" in argv[-1]


def test_operator_session_flags_win_over_injection(isolated_store) -> None:
    backend = _backend("claude", extra_args=["--resume", "operator-id"])
    request = AgentLoopRequest(
        prompt="Q",
        session_id="s1",
        history=[{"role": "user", "content": "earlier"}],
    )
    argv = backend.build_argv(request, resume_id="agent-9", fresh_session_id="u1")
    assert argv.count("--resume") == 1  # the operator's, untouched
    assert "--session-id" not in argv
    # Degraded, not silent: with injection skipped the folded transcript is
    # restored — it is the only history channel left.
    assert "Conversation so far" in argv[-1]


def test_joined_session_flag_form_also_counts(isolated_store) -> None:
    backend = _backend("claude", extra_args=["--session-id=operator-id"])
    argv = backend.build_argv(_request(), resume_id="agent-9", fresh_session_id="u1")
    assert "--session-id" not in argv[1:-1] or argv.count("--session-id") == 1


def test_codex_non_exec_command_falls_back_to_folded_history(isolated_store) -> None:
    """A codex preset pointed at a wrapper that is not `exec` cannot take the
    resume splice — the turn must degrade to the folded transcript instead of
    silently losing all context."""
    backend = _backend(
        "codex",
        name="codex",
        command="codex-wrap",
        base_args=["run", "--json"],
    )
    request = AgentLoopRequest(
        prompt="Q",
        session_id="s1",
        history=[{"role": "user", "content": "earlier"}],
    )
    argv = backend.build_argv(request, resume_id="thread-1")
    assert "resume" not in argv[:3]
    assert "Conversation so far" in argv[-1]


def test_codex_resume_splices_the_subcommand(isolated_store) -> None:
    backend = _backend(
        "codex",
        name="codex",
        command="codex",
        base_args=["exec", "--json"],
    )
    argv = backend.build_argv(_request(), resume_id="thread-1")
    assert argv == ["codex", "exec", "resume", "thread-1", "--json", "Q"]


def test_codex_capture_and_store(isolated_store) -> None:
    state: dict = {}
    assert translate_codex({"type": "thread.started", "thread_id": "th-1"}, state) == []
    assert state["agent_session_id"] == "th-1"
    backend = _backend(
        "codex", name="codex", command="codex", base_args=["exec", "--json"], text_output=True
    )
    backend._remember_session(_request(), resume_id="", fresh_session_id="", state=state)
    assert agent_session_store.load_agent_session("codex:s1") == "th-1"


def test_no_session_id_disables_resume(isolated_store) -> None:
    backend = _backend("claude")
    resume_id, fresh_id, key = backend._resolve_resume(AgentLoopRequest(prompt="Q"))
    assert (resume_id, fresh_id, key) == ("", "", "")


# ---------------------------------------------------------------------------
# L2: a failed resume retries fresh, observably, exactly once
# ---------------------------------------------------------------------------

_FLAKY_RESUME_CLI = """\
import sys
if "--resume" in sys.argv:
    print("No conversation found with session ID gone-1", file=sys.stderr)
    sys.exit(1)
print("fresh answer")
sys.exit(0)
"""


@pytest.mark.asyncio
async def test_failed_resume_retries_fresh(isolated_store, tmp_path) -> None:
    script = tmp_path / "fake_claude.py"
    script.write_text(_FLAKY_RESUME_CLI, encoding="utf-8")
    agent_session_store.save_agent_session("claude-code:s1", agent_session_id="gone-1")
    backend = _backend(
        "claude",
        command=sys.executable,
        base_args=["-u", str(script)],
        text_output=True,
    )

    events = [event async for event in backend.run(_request(content="hi"))]

    kinds = [event.kind for event in events]
    assert "progress" in kinds  # the reset is observable
    assert "content" in kinds
    contents = [event.text for event in events if event.kind == "content"]
    assert [text.strip() for text in contents] == ["fresh answer"]
    # The dead mapping was dropped and the fresh (minted) session recorded.
    refetched = agent_session_store.load_agent_session("claude-code:s1")
    assert refetched and refetched != "gone-1"


@pytest.mark.asyncio
async def test_healthy_resume_never_retries(isolated_store, tmp_path) -> None:
    calls: list[list[str]] = []

    script = tmp_path / "fake_claude_ok.py"
    script.write_text(
        "import json, sys\n"
        "calls = open(" + json.dumps(str(tmp_path / "calls")) + ", 'a')\n"
        "calls.write(' '.join(sys.argv[1:]) + chr(10))\n"
        "sys.stdout.write(json.dumps({'type': 'result', 'subtype': 'success',"
        " 'result': 'ok', 'session_id': 'keep-1', 'is_error': False}) + chr(10))\n",
        encoding="utf-8",
    )
    agent_session_store.save_agent_session("claude-code:s1", agent_session_id="alive-1")
    backend = _backend(
        "claude",
        command=sys.executable,
        base_args=["-u", str(script)],
        text_output=True,
    )

    events = [event async for event in backend.run(_request(content="hi"))]

    assert [event.kind for event in events] == ["content"]
    assert "progress" not in [event.kind for event in events]
    recorded = (tmp_path / "calls").read_text()
    assert "--resume" in recorded and "alive-1" in recorded
    # Mapping untouched.
    assert agent_session_store.load_agent_session("claude-code:s1") == "alive-1"
    del calls


# ---------------------------------------------------------------------------
# M4: opencode v2 (`run -s <id>`, sessionID on every event)
# ---------------------------------------------------------------------------


def test_opencode_resume_injects_the_session_flag(isolated_store) -> None:
    backend = _backend(
        "opencode",
        name="opencode",
        command="opencode",
        base_args=["run", "--format", "json"],
    )
    argv = backend.build_argv(_request(), resume_id="ses_abc")
    assert argv == ["opencode", "run", "--format", "json", "-s", "ses_abc", "Q"]


def test_opencode_capture_reads_sessionid_off_any_event(isolated_store) -> None:
    """opencode stamps every NDJSON event (errors included) with the
    sessionID; the capture is translator-independent."""
    backend = _backend(
        "opencode",
        name="opencode",
        command="opencode",
        base_args=["run", "--format", "json"],
    )
    state: dict = {}
    assert (
        backend._line_events(b'{"type":"error","sessionID":"ses_1","error":{"message":"x"}}', state)
        == []
    )
    assert state["agent_session_id"] == "ses_1"
    # Non-opencode backends must not pick the key up.
    plain = _backend("")
    state2: dict = {}
    plain._line_events(b'{"type":"error","sessionID":"ses_2"}', state2)
    assert "agent_session_id" not in state2


def test_opencode_capture_survives_the_msg_wrapper(isolated_store) -> None:
    backend = _backend(
        "opencode",
        name="opencode",
        command="opencode",
        base_args=["run", "--format", "json"],
    )
    state: dict = {}
    backend._line_events(b'{"msg": {"type": "x", "sessionID": "ses_wrapped"}}', state)
    assert state["agent_session_id"] == "ses_wrapped"


# ---------------------------------------------------------------------------
# translate_opencode: shapes sampled from v2.0.9
# ---------------------------------------------------------------------------


def test_opencode_text_part_becomes_content() -> None:
    from kagweb.services.agent_loop.cli_backend import translate_opencode

    state: dict = {}
    assert (
        translate_opencode(
            {"type": "step_start", "sessionID": "s", "part": {"type": "step-start"}}, state
        )
        == []
    )
    events = translate_opencode(
        {"type": "text", "sessionID": "s", "part": {"type": "text", "text": "OK"}}, state
    )
    assert [(e.kind, e.text) for e in events] == [("content", "OK")]


def test_opencode_tool_part_degrades_to_progress() -> None:
    from kagweb.services.agent_loop.cli_backend import translate_opencode

    state: dict = {}
    events = translate_opencode(
        {
            "type": "tool",
            "sessionID": "s",
            "part": {"type": "tool", "tool": "bash", "state": {"status": "running"}},
        },
        state,
    )
    assert [e.kind for e in events] == ["progress"]
    assert "bash" in events[0].text


def test_opencode_unknown_type_is_skipped() -> None:
    from kagweb.services.agent_loop.cli_backend import translate_opencode

    state: dict = {}
    assert translate_opencode({"type": "mystery", "sessionID": "s"}, state) == []
