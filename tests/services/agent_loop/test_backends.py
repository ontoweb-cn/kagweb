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
from kagweb.services.agent_loop.http_backend import HttpAgentLoopBackend
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
    trace can pair them."""
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
    assert events[0].name == "Bash"
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
    assert events[0].name == "Bash"
    assert events[0].text == "boom"
    assert events[0].data == {"id": "call_1", "is_error": True}


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
