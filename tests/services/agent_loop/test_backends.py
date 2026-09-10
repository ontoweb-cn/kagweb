"""Agent-loop backend tests: CLI subprocess lifecycle, HTTP stream parsing,
translator mappings, and the settings-driven factory."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys
import textwrap
from typing import Any

import httpx
import pytest

from kagweb.services.agent_loop import AgentLoopError, build_agent_loop_backend
from kagweb.services.agent_loop.cli_backend import (
    TRANSLATORS,
    CliAgentLoopBackend,
    translate_claude_code,
    translate_codex,
    translate_generic,
)
from kagweb.services.agent_loop.http_backend import HttpAgentLoopBackend, RunsAgentLoopBackend
from kagweb.services.agent_loop.protocol import AgentLoopRequest

pytestmark = pytest.mark.asyncio


def _cli_backend(script: Path, *, timeout: float = 30.0, extra_args: list[str] | None = None):
    return CliAgentLoopBackend(
        name="fake-cli",
        command=sys.executable,
        base_args=["-u", str(script)],
        extra_args=extra_args or [],
        env={},
        timeout_seconds=timeout,
        translator=TRANSLATORS["claude-code"],
    )


def _request(prompt: str = "hi", **kwargs: Any) -> AgentLoopRequest:
    return AgentLoopRequest(prompt=prompt, session_id="s1", language="en", **kwargs)


def _write_agent(tmp_path: Path, body: str) -> Path:
    script = tmp_path / "agent.py"
    script.write_text(textwrap.dedent(body), encoding="utf-8")
    return script


# ---------------------------------------------------------------------------
# CLI backend — subprocess lifecycle
# ---------------------------------------------------------------------------


async def test_cli_backend_streams_translated_events(tmp_path: Path) -> None:
    workdir = tmp_path / "work"
    workdir.mkdir()
    script = _write_agent(
        tmp_path,
        """
        import json, os, sys
        prompt = sys.argv[-1]
        assert prompt == "hello agent", prompt
        print(json.dumps({"type": "assistant", "message": {"content": [
            {"type": "thinking", "thinking": os.getcwd()},
            {"type": "text", "text": "echo: " + prompt},
            {"type": "tool_use", "name": "bash", "input": {"command": "ls"}},
        ]}}), flush=True)
        print(json.dumps({"type": "user", "message": {"content": [
            {"type": "tool_result", "content": "file.txt"},
        ]}}), flush=True)
        print(json.dumps({"type": "result", "result": "echo: " + prompt,
                          "usage": {"input_tokens": 3, "output_tokens": 4},
                          "total_cost_usd": 0.01, "subtype": "success"}), flush=True)
        """,
    )

    backend = _cli_backend(script)
    events = [event async for event in backend.run(_request("hello agent", workdir=str(workdir)))]

    kinds = [(e.kind, e.text, e.name) for e in events]
    assert kinds == [
        ("thinking", str(workdir), ""),
        ("content", "echo: hello agent", ""),
        ("tool_call", "", "bash"),
        ("tool_result", "file.txt", ""),
        ("usage", "", ""),
    ]
    # The deduplicated result text (equal to the last assistant text) must
    # not be emitted twice.
    assert sum(1 for e in events if e.kind == "content") == 1
    assert events[-1].data.get("input_tokens") == 3
    # Claude's result line reports the whole invocation, so the counters are a
    # coherent pass total.
    assert events[-1].data.get("usage_scope") == "pass"


async def test_cli_backend_nonzero_exit_fails_turn(tmp_path: Path) -> None:
    script = _write_agent(
        tmp_path,
        """
        import sys
        print("boom details", file=sys.stderr)
        sys.exit(3)
        """,
    )
    backend = _cli_backend(script)
    with pytest.raises(AgentLoopError) as excinfo:
        async for _ in backend.run(_request()):
            pass
    assert "code 3" in str(excinfo.value)
    assert "boom details" in str(excinfo.value)


async def test_cli_backend_child_env_is_allowlisted(tmp_path, monkeypatch) -> None:
    """Server-environment secrets must not leak into the agent subprocess."""
    monkeypatch.setenv("KAGWEB_TEST_SECRET", "s3cr3t")
    monkeypatch.setenv("AUTH_PASSWORD_HASH", "$2b$12$leak")
    monkeypatch.setenv("POCKETBASE_ADMIN_PASSWORD", "leak")
    # Guard the passthrough basics at the same time.
    monkeypatch.setenv("PATH", "/usr/bin:/bin")  # allowlisted on every OS
    script = _write_agent(
        tmp_path,
        """
        import json, os
        env = dict(os.environ)
        leaks = [k for k in ("KAGWEB_TEST_SECRET", "AUTH_PASSWORD_HASH",
                             "POCKETBASE_ADMIN_PASSWORD") if k in env]
        assert not leaks, leaks
        assert "PATH" in env
        print(json.dumps({"type": "assistant", "message": {"content": [
            {"type": "text", "text": "env ok"},
        ]}}), flush=True)
        """,
    )
    backend = _cli_backend(script)
    events = [e async for e in backend.run(_request())]
    assert events and events[0].kind == "content" and events[0].text == "env ok"


async def test_cli_backend_configured_env_passes_through(tmp_path) -> None:
    script = _write_agent(
        tmp_path,
        """
        import json, os
        assert os.environ.get("ANTHROPIC_API_KEY") == "operator-key", os.environ
        print(json.dumps({"type": "text", "text": "key ok"}), flush=True)
        """,
    )
    backend = CliAgentLoopBackend(
        name="fake-cli",
        command=sys.executable,
        base_args=["-u", str(script)],
        extra_args=[],
        env={"ANTHROPIC_API_KEY": "operator-key"},
        timeout_seconds=30.0,
        translator=TRANSLATORS["generic"],
    )
    events = [e async for e in backend.run(_request())]
    assert events and events[0].text == "key ok"


async def test_cli_backend_timeout_kills_process(tmp_path: Path) -> None:
    script = _write_agent(
        tmp_path,
        """
        import time
        print("{}", flush=True)
        time.sleep(30)
        """,
    )
    backend = _cli_backend(script, timeout=1.0)
    with pytest.raises(AgentLoopError) as excinfo:
        async for _ in backend.run(_request()):
            pass
    # Message wording is localized; the backend attribute is not.
    assert excinfo.value.backend == "fake-cli"


async def test_stderr_cleanup_never_masks_the_turns_own_failure(
    tmp_path: Path, monkeypatch
) -> None:
    """A still-pending stderr drainer must not replace the turn's real error.

    Cleanup cancels that task and awaits it, which re-raises CancelledError —
    a BaseException, so `suppress(Exception)` does not catch it. When the task
    happened to have finished (the killed child closes the pipe and the drainer
    sees EOF) the await returned quietly; when it had not, CancelledError
    escaped the finally and the caller received it *instead of* AgentLoopError.
    That is a race, and it failed CI on 3.14 before this was fixed.

    The drainer is held pending here so the race is deterministic rather than
    timing-dependent — the timeout must still surface as AgentLoopError.
    """

    async def _never_finishes(proc, tail) -> None:  # noqa: ANN001 - matches the seam
        await asyncio.Event().wait()

    monkeypatch.setattr(CliAgentLoopBackend, "_drain_stderr", staticmethod(_never_finishes))

    script = _write_agent(
        tmp_path,
        """
        import time
        print("{}", flush=True)
        time.sleep(30)
        """,
    )
    backend = _cli_backend(script, timeout=1.0)

    with pytest.raises(AgentLoopError) as excinfo:
        async for _ in backend.run(_request()):
            pass

    assert excinfo.value.backend == "fake-cli"


async def test_cli_backend_closing_stream_kills_process(tmp_path: Path) -> None:
    marker = tmp_path / "survived.marker"
    script = _write_agent(
        tmp_path,
        f"""
        import json, time
        from pathlib import Path
        print(json.dumps({{"type": "assistant", "message": {{"content": [
            {{"type": "text", "text": "first"}},
        ]}}}}), flush=True)
        time.sleep(30)
        Path({str(marker)!r}).write_text("alive", encoding="utf-8")
        """,
    )
    backend = _cli_backend(script)
    stream = backend.run(_request())
    first = await stream.__anext__()
    assert first.kind == "content" and first.text == "first"
    await stream.aclose()

    # Give the kill a moment to land; the marker must never appear.
    for _ in range(20):
        assert not marker.exists()
        await asyncio.sleep(0.05)


async def test_cli_backend_missing_command_raises(tmp_path: Path) -> None:
    backend = CliAgentLoopBackend(
        name="missing",
        command="definitely-not-a-real-command-xyz",
        base_args=[],
        extra_args=[],
        env={},
        timeout_seconds=5,
        translator=TRANSLATORS["generic"],
    )
    with pytest.raises(AgentLoopError) as excinfo:
        async for _ in backend.run(_request()):
            pass
    # Message wording is localized; the backend name appears in both.
    assert excinfo.value.backend == "missing"
    assert "missing" in str(excinfo.value)


# ---------------------------------------------------------------------------
# HTTP backend — stream parsing and error mapping
# ---------------------------------------------------------------------------


def _http_backend(handler, *, api_key: str = "secret", timeout: float = 30.0):
    return HttpAgentLoopBackend(
        name="fake-http",
        url="http://agent.local",
        turn_path="/agent/turn",
        api_key=api_key,
        headers={},
        timeout_seconds=timeout,
        transport=httpx.MockTransport(handler),
    )


async def test_http_backend_ndjson_neutral_events() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["auth"] = request.headers.get("Authorization")
        seen["body"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(
            200,
            headers={"content-type": "application/x-ndjson"},
            content=(
                b'{"kind": "thinking", "text": "pondering"}\n'
                b'{"kind": "content", "text": "answer part 1"}\n'
                b'{"kind": "tool_call", "name": "shell", "data": {"args": {"command": "ls"}}}\n'
                b'{"kind": "tool_result", "name": "shell", "text": "files"}\n'
                b'{"kind": "usage", "data": {"input_tokens": 7}}\n'
            ),
        )

    backend = _http_backend(handler)
    events = [
        e
        async for e in backend.run(_request("q", history=[{"role": "user", "content": "earlier"}]))
    ]

    assert seen["path"] == "/agent/turn"
    assert seen["auth"] == "Bearer secret"
    assert seen["body"]["prompt"] == "q"
    assert seen["body"]["history"] == [{"role": "user", "content": "earlier"}]

    kinds = [e.kind for e in events]
    assert kinds == ["thinking", "content", "tool_call", "tool_result", "usage"]
    assert events[2].name == "shell"
    assert events[2].data == {"args": {"command": "ls"}}


async def test_http_backend_sse_events() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=(
                b": keepalive\n\n"
                b'data: {"kind": "content", "text": "hello"}\n\n'
                b'data: {"kind": "content", "text": " world"}\n\n'
            ),
        )

    backend = _http_backend(handler)
    events = [e async for e in backend.run(_request())]
    assert [e.text for e in events] == ["hello", " world"]


async def test_http_backend_discards_oversized_lines() -> None:
    """A runaway NDJSON line must be dropped without killing the stream."""
    from kagweb.services.agent_loop.protocol import MAX_LINE_BYTES

    oversized = b"x" * (MAX_LINE_BYTES + 1024)
    body = (
        b'{"kind": "content", "text": "before"}\n'
        + oversized
        + b"\n"
        + b'{"kind": "content", "text": "after"}\n'
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "application/x-ndjson"}, content=body)

    backend = _http_backend(handler)
    events = [e async for e in backend.run(_request())]
    assert [e.text for e in events] == ["before", "after"]


async def test_http_backend_discards_oversized_sse_event() -> None:
    from kagweb.services.agent_loop.protocol import MAX_LINE_BYTES

    oversized = b"x" * (MAX_LINE_BYTES + 1024)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=(
                b'data: {"kind": "content", "text": "kept"}\n\n'
                b"data: " + oversized + b"\n\n"
                b'data: {"kind": "content", "text": "also kept"}\n\n'
            ),
        )

    backend = _http_backend(handler)
    events = [e async for e in backend.run(_request())]
    assert [e.text for e in events] == ["kept", "also kept"]


async def test_http_backend_generic_fallback_events() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        # Vendor-shaped lines (no neutral "kind") go through the heuristic
        # translator instead of being dropped.
        return httpx.Response(
            200,
            headers={"content-type": "application/x-ndjson"},
            content=(
                b'{"type": "text", "text": "plain"}\n'
                b'{"type": "tool_use", "name": "grep", "input": {"q": "x"}}\n'
                b'{"event": "log", "message": "noise but not an error"}\n'
            ),
        )

    backend = _http_backend(handler)
    events = [e async for e in backend.run(_request())]
    kinds = [(e.kind, e.text, e.name) for e in events]
    assert kinds[0] == ("content", "plain", "")
    assert kinds[1] == ("tool_call", "", "grep")


async def test_http_backend_error_status_fails_turn() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, content=b"agent restarting")

    backend = _http_backend(handler)
    with pytest.raises(AgentLoopError) as excinfo:
        async for _ in backend.run(_request()):
            pass
    assert "503" in str(excinfo.value)
    assert "agent restarting" in str(excinfo.value)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def test_factory_none_when_unconfigured() -> None:
    assert build_agent_loop_backend({}) is None
    assert build_agent_loop_backend({"backend": ""}) is None


def test_factory_rejects_unknown_backend() -> None:
    with pytest.raises(AgentLoopError):
        build_agent_loop_backend({"backend": "does-not-exist"})


def test_factory_cli_preset_overrides() -> None:
    backend = build_agent_loop_backend(
        {"backend": "claude-code", "command": "/opt/claude", "timeout_seconds": 60}
    )
    assert isinstance(backend, CliAgentLoopBackend)
    assert backend.command == "/opt/claude"
    assert backend.timeout_seconds == 60


def test_factory_custom_cli_prompt_placeholder() -> None:
    backend = build_agent_loop_backend(
        {"backend": "custom-cli", "command": "a", "args": ["--prompt={prompt}"]}
    )
    assert backend.build_argv(_request("Q")) == ["a", "--prompt=Q"]


def test_factory_http_requires_url() -> None:
    with pytest.raises(AgentLoopError):
        build_agent_loop_backend({"backend": "agentscope"})
    backend = build_agent_loop_backend(
        {"backend": "agentscope", "url": "http://as:9000", "turn_path": "/v1/chat"}
    )
    assert isinstance(backend, HttpAgentLoopBackend)
    assert backend.endpoint() == "http://as:9000/v1/chat"


def test_intellect_http_presets_ride_the_run_channel() -> None:
    """Both Intellect HTTP presets must select the runs transport.

    ``intellect-team`` used to carry no ``turn_path``/``protocol``, so it fell
    back to the preset default ``/agent/turn`` — an endpoint the service does
    not expose, making every turn a guaranteed 404. The failure was invisible
    because the transport is chosen from the very fields that were missing.
    """
    for preset in ("intellect-team", "intellect-runs"):
        backend = build_agent_loop_backend({"backend": preset, "url": "http://intellect.test"})
        assert isinstance(backend, RunsAgentLoopBackend), preset
        assert backend.turn_path == "/v1/runs", preset


# ---------------------------------------------------------------------------
# Translators
# ---------------------------------------------------------------------------


def test_claude_code_translator_dedupes_result_text() -> None:
    state: dict[str, Any] = {}
    events = translate_claude_code(
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "final"}]}}, state
    )
    events += translate_claude_code({"type": "result", "result": "final"}, state)
    assert [e.kind for e in events] == ["content"]

    events = translate_claude_code({"type": "result", "result": "different"}, state)
    assert [e.kind for e in events] == ["content"]
    assert events[0].text == "different"


def test_claude_code_translator_error_subtype() -> None:
    events = translate_claude_code(
        {"type": "result", "result": "x", "subtype": "error_during_execution"}, {}
    )
    assert any(e.kind == "error" for e in events)


def test_claude_code_translator_pairs_tool_call_and_result() -> None:
    """A tool_result block carries only the tool_use id, so the call's name is
    remembered to label the result — and the id travels on both events so the
    trace can pair them. The name is KAGWeb's, not the vendor's."""
    state: dict[str, Any] = {}
    events = translate_claude_code(
        {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "tool_use", "id": "call_1", "name": "Bash", "input": {"command": "ls"}}
                ]
            },
        },
        state,
    )
    assert [e.kind for e in events] == ["tool_call"]
    assert events[0].name == "exec"
    assert events[0].data == {"args": {"command": "ls"}, "id": "call_1"}

    events = translate_claude_code(
        {
            "type": "user",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "call_1",
                        "is_error": True,
                        "content": "boom",
                    }
                ]
            },
        },
        state,
    )
    assert [e.kind for e in events] == ["tool_result"]
    assert events[0].name == "exec"
    assert events[0].text == "boom"
    assert events[0].data == {"id": "call_1", "is_error": True}


def test_claude_code_tool_names_map_to_kagweb_vocabulary() -> None:
    """The activity row's verb and chip come from KAGWeb's tool vocabulary, so
    the vendor's name *and* its argument keys are translated at this boundary.
    An unknown tool still travels under the vendor's own name."""

    def translate(name: str, tool_input: dict[str, Any]) -> Any:
        events = translate_claude_code(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "tool_use", "id": "call_1", "name": name, "input": tool_input}
                    ]
                },
            },
            {},
        )
        return events[0]

    assert translate("Read", {"file_path": "a/b.py"}).name == "read_file"
    assert translate("Read", {"file_path": "a/b.py"}).data["args"] == {"path": "a/b.py"}
    assert translate("Skill", {"skill": "dataviz", "args": "x"}).name == "read_skill"
    assert translate("Skill", {"skill": "dataviz", "args": "x"}).data["args"] == {
        "name": "dataviz",
        "args": "x",
    }
    assert translate("Glob", {"pattern": "**/*.ts"}).name == "glob"
    assert translate("Glob", {"pattern": "**/*.ts"}).data["args"] == {"pattern": "**/*.ts"}
    assert translate("Grep", {"pattern": "TODO"}).name == "grep"
    assert translate("MultiEdit", {"file_path": "a.py"}).name == "edit_file"
    # Unmapped vendor tools keep their own name and args.
    assert translate("TodoWrite", {"todos": []}).name == "TodoWrite"
    assert translate("TodoWrite", {"todos": []}).data["args"] == {"todos": []}


def test_codex_translator_names_shell_and_patch_in_kagweb_vocabulary() -> None:
    """Codex's `shell`/`file_change` items map onto the same row vocabulary,
    and the command travels in `args` so the row's chip can show it."""
    events = translate_codex(
        {
            "msg": {
                "type": "item.started",
                "item": {"type": "command_execution", "id": "item-1", "command": "ls"},
            }
        },
        {},
    )
    assert events[0].name == "exec"
    assert events[0].data["args"] == {"command": "ls"}
    assert events[0].data["id"] == "item-1"

    events = translate_codex(
        {
            "msg": {
                "type": "item.started",
                "item": {"type": "file_change", "changes": [{"path": "a.py"}]},
            }
        },
        {},
    )
    assert events[0].name == "edit_file"
    assert events[0].data["args"] == {"path": "a.py"}


def test_codex_item_updated_is_not_a_second_call() -> None:
    """`item.updated` reports progress on an item that already opened. Treating
    it as a new call renders duplicate rows that never receive a result."""
    assert (
        translate_codex(
            {
                "msg": {
                    "type": "item.updated",
                    "item": {"type": "command_execution", "command": "ls"},
                }
            },
            {},
        )
        == []
    )


def test_codex_completed_command_reports_failure() -> None:
    """The trace row reads `is_error`; without it a failed command renders as a
    clean finish."""
    failed = translate_codex(
        {
            "msg": {
                "type": "item.completed",
                "item": {
                    "type": "command_execution",
                    "aggregated_output": "1 failed",
                    "exit_code": 1,
                },
            }
        },
        {},
    )
    assert failed[0].kind == "tool_result"
    assert failed[0].data["is_error"] is True

    ok = translate_codex(
        {
            "msg": {
                "type": "item.completed",
                "item": {"type": "command_execution", "aggregated_output": "ok", "exit_code": 0},
            }
        },
        {},
    )
    assert ok[0].data["is_error"] is False


def test_codex_mcp_result_reports_its_output_not_its_arguments() -> None:
    events = translate_codex(
        {
            "msg": {
                "type": "item.completed",
                "item": {
                    "type": "mcp_tool_call",
                    "tool": "search",
                    "arguments": {"q": "x"},
                    "result": {"content": [{"type": "text", "text": "42 hits"}]},
                },
            }
        },
        {},
    )
    assert events[0].kind == "tool_result"
    assert events[0].text == "42 hits"


def test_claude_code_translator_summarises_image_tool_results() -> None:
    """An image tool_result would otherwise inline its base64 verbatim."""
    events = translate_claude_code(
        {
            "type": "user",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "call_1",
                        "content": [
                            {"type": "text", "text": "screenshot taken"},
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": "image/png",
                                    "data": "A" * 4096,
                                },
                            },
                        ],
                    }
                ]
            },
        },
        {},
    )
    assert [e.kind for e in events] == ["tool_result"]
    assert events[0].text == "screenshot taken\n[image image/png, 4096 base64 chars]"
    assert "AAAA" not in events[0].text


def test_claude_code_translator_surfaces_the_init_line() -> None:
    """The init line names the model and permission mode the turn ran under."""
    events = translate_claude_code(
        {
            "type": "system",
            "subtype": "init",
            "model": "claude-sonnet-5",
            "permissionMode": "acceptEdits",
            "tools": ["Bash", "Read"],
            "session_id": "vendor-1",
        },
        {},
    )
    assert [e.kind for e in events] == ["progress"]
    assert events[0].text == "model=claude-sonnet-5 · permission=acceptEdits · tools=2"

    # Other system lines carry no user-facing payload and stay silent.
    assert translate_claude_code({"type": "system", "subtype": "hook"}, {}) == []


def test_cli_backend_folds_history_into_the_prompt() -> None:
    """argv is a CLI's only input channel, so the transcript travels with the
    prompt; unknown roles are ignored rather than mislabelled."""
    backend = build_agent_loop_backend(
        {"backend": "custom-cli", "command": "a", "args": ["{prompt}"]}
    )
    request = _request(
        "What is my name?",
        history=[
            {"role": "user", "content": "My name is Alice"},
            {"role": "assistant", "content": "Hi Alice"},
            {"role": "tool", "content": "ignored"},
        ],
    )
    assert backend.build_argv(request) == [
        "a",
        "Conversation so far:\n\nUser: My name is Alice\n\nAssistant: Hi Alice\n\nWhat is my name?",
    ]


def test_cli_backend_without_history_uses_the_bare_prompt() -> None:
    backend = build_agent_loop_backend(
        {"backend": "custom-cli", "command": "a", "args": ["{prompt}"]}
    )
    assert backend.build_argv(_request("Q")) == ["a", "Q"]


def test_codex_translator_event_types() -> None:
    state: dict[str, Any] = {}
    out: list[tuple[str, str]] = []
    for line in (
        {"msg": {"type": "agent_message", "message": "working"}},
        {"msg": {"type": "item.started", "item": {"type": "command_execution", "command": "ls"}}},
        {
            "msg": {
                "type": "item.completed",
                "item": {"type": "command_execution", "aggregated_output": "files", "exit_code": 0},
            }
        },
        {
            "msg": {
                "type": "token_count",
                "info": {
                    "total_token_usage": {"input_tokens": 5},
                    "last_token_usage": {"output_tokens": 6},
                },
            }
        },
        {"msg": {"type": "something_new", "data": "future kind"}},
    ):
        out.extend((e.kind, e.text) for e in translate_codex(line, state))
    assert out == [
        ("content", "working"),
        ("tool_call", "ls"),
        ("tool_result", "files"),
        ("usage", ""),
    ]


def test_generic_translator_maps_common_shapes() -> None:
    state: dict[str, Any] = {}
    assert translate_generic({"delta": "chunk"}, state)[0].kind == "content"
    assert translate_generic({"type": "tool_result", "result": "r"}, state)[0].kind == "tool_result"
    assert translate_generic({"type": "error", "error": "bad"}, state)[0].kind == "error"
    assert translate_generic({"type": "log", "ignored": True}, state) == []


# ---------------------------------------------------------------------------
# Text-output mode (plain-text final answer CLIs, e.g. `intellect chat -Q`)
# ---------------------------------------------------------------------------


async def test_cli_backend_text_output_yields_one_content_block(tmp_path: Path) -> None:
    script = _write_agent(
        tmp_path,
        """
            import sys
            sys.stdout.write("para one\\n\\npara two\\n")
            sys.stderr.write("noisy progress ignored\\n")
        """,
    )
    backend = CliAgentLoopBackend(
        name="text-cli",
        command=sys.executable,
        base_args=["-u", str(script)],
        extra_args=[],
        env={},
        timeout_seconds=30,
        translator=TRANSLATORS["generic"],
        text_output=True,
    )
    events = [event async for event in backend.run(_request("hi"))]
    # One whole content block (content events are blocks, not deltas), and
    # the non-JSON stdout did NOT get dropped like it would in NDJSON mode.
    assert [(event.kind, event.text) for event in events] == [("content", "para one\n\npara two\n")]


async def test_cli_backend_text_output_failure_raises(tmp_path: Path) -> None:
    script = _write_agent(
        tmp_path,
        """
            import sys
            sys.stdout.write("partial")
            sys.stderr.write("boom reason\\n")
            sys.exit(1)
        """,
    )
    backend = CliAgentLoopBackend(
        name="text-cli",
        command=sys.executable,
        base_args=["-u", str(script)],
        extra_args=[],
        env={},
        timeout_seconds=30,
        translator=TRANSLATORS["generic"],
        text_output=True,
    )
    with pytest.raises(AgentLoopError) as excinfo:
        async for _ in backend.run(_request("hi")):
            pass
    assert "boom reason" in str(excinfo.value)


def test_codex_token_count_marks_the_counters_cumulative() -> None:
    """Codex's input counter is a running total while output is the last
    message's, so the pair is not a coherent pass figure — the scope says so
    and the DSL export then omits a synthesized total."""
    events = translate_codex(
        {
            "msg": {
                "type": "token_count",
                "info": {
                    "total_token_usage": {"input_tokens": 5000, "output_tokens": 100},
                    "last_token_usage": {"input_tokens": 120, "output_tokens": 220},
                },
            }
        },
        {},
    )

    assert events[0].kind == "usage"
    assert events[0].data["input_tokens"] == 5000
    assert events[0].data["output_tokens"] == 220
    assert events[0].data["usage_scope"] == "cumulative"


def test_usage_event_is_not_emitted_without_counters() -> None:
    """The scope stamp must not keep the all-None guard alive: a result line
    with no counters produces no usage event (and no scope-only payload)."""
    events = translate_claude_code(
        {
            "type": "result",
            "result": "done",
            "usage": {"input_tokens": None, "output_tokens": None},
        },
        {},
    )

    assert [event.kind for event in events] == ["content"]


def test_codex_token_count_without_counters_emits_nothing() -> None:
    assert translate_codex({"msg": {"type": "token_count", "info": {}}}, {}) == []


# ---------------------------------------------------------------------------
# The profile's model field
# ---------------------------------------------------------------------------


def test_factory_threads_the_model_onto_every_family() -> None:
    """The model is a profile field, so it must reach CLI, ACP and both HTTP
    families — not just the one that happens to use it most."""
    cli = build_agent_loop_backend(
        {"backend": "claude-code", "command": "claude", "model": "claude-sonnet-5"}
    )
    assert cli.model == "claude-sonnet-5"

    http = build_agent_loop_backend({"backend": "hermes", "url": "http://h:1", "model": "qwen-max"})
    assert http.model == "qwen-max"

    runs = build_agent_loop_backend(
        {"backend": "intellect-runs", "url": "http://r:1", "model": "gpt-5"}
    )
    assert runs.model == "gpt-5"


def test_an_absent_model_leaves_every_family_unconfigured() -> None:
    # No model key at all → empty, which is what keeps existing deployments
    # byte-identical (no `--model` arg, no `model` in the request body).
    for spec in (
        {"backend": "claude-code", "command": "claude"},
        {"backend": "hermes", "url": "http://h:1"},
    ):
        assert build_agent_loop_backend(spec).model == ""


def test_cli_substitutes_the_model_placeholder() -> None:
    backend = build_agent_loop_backend(
        {
            "backend": "custom-cli",
            "command": "a",
            "model": "big-model",
            "args": ["--model={model}", "{prompt}"],
        }
    )
    assert backend.build_argv(_request("Q")) == ["a", "--model=big-model", "Q"]


def test_cli_drops_an_argument_that_references_an_unset_model() -> None:
    """No model configured → the whole `--model={model}` element disappears, so
    the CLI falls back to its own configured default instead of receiving a
    literal placeholder (or a bare `--model` flag)."""
    backend = build_agent_loop_backend(
        {
            "backend": "custom-cli",
            "command": "a",
            "args": ["--model={model}", "{prompt}"],
        }
    )
    assert backend.build_argv(_request("Q")) == ["a", "Q"]


def test_cli_keeps_other_args_when_the_model_is_unset() -> None:
    # Only the element carrying {model} is dropped; the rest survive.
    backend = build_agent_loop_backend(
        {
            "backend": "custom-cli",
            "command": "a",
            "args": ["--verbose", "--model={model}", "--json", "{prompt}"],
        }
    )
    assert backend.build_argv(_request("Q")) == ["a", "--verbose", "--json", "Q"]


async def test_http_request_carries_the_model_only_when_configured() -> None:
    """The model key is omitted when unset, so a deployment that never sets one
    sends exactly the body it sent before the field existed."""
    bodies: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, text='{"kind": "content", "text": "ok"}\n')

    with_model = _http_backend(handler)
    with_model.model = "qwen-max"
    [event async for event in with_model.run(_request("hi"))]
    assert bodies[-1]["model"] == "qwen-max"

    without_model = _http_backend(handler)
    [event async for event in without_model.run(_request("hi"))]
    assert "model" not in bodies[-1]
