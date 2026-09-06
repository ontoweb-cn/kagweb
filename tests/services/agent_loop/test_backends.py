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
