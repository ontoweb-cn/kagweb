"""CLI-family agent-loop backend: one subprocess per turn.

Targets terminal agent CLIs that take a prompt and emit newline-delimited
JSON on stdout (``--print`` / ``exec`` / ``run`` non-interactive modes).
Vendor differences are confined to two small pieces: the argv template
(:data:`kagweb.services.agent_loop.builtin.PRESETS`) and a line translator
function mapping one parsed JSON object to neutral
:class:`~kagweb.services.agent_loop.protocol.AgentLoopEvent` objects.

Security: the subprocess inherits the server process's privileges and the
per-session workspace under ``data/user/workspace``. That is the right
shape for single-operator / local deployments; multi-user deployments
should use the HTTP family so the loop runs in its own service, not
inside the app.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import nullcontext, suppress
import json
import logging
import os
from typing import Any, Callable

from kagweb.services.i18n import t

from .protocol import AgentLoopBackend, AgentLoopError, AgentLoopEvent, AgentLoopRequest

logger = logging.getLogger(__name__)

#: Keep stderr tails bounded: they surface in failed-turn error text.
_STDERR_TAIL_LIMIT = 2000
#: Hard cap on a single JSON line (agents can inline big tool outputs).
_MAX_LINE_BYTES = 4 * 1024 * 1024

Translator = Callable[[dict[str, Any], dict[str, Any]], list[AgentLoopEvent]]


# ---------------------------------------------------------------------------
# Vendor line translators — pure functions, (line_object, run_state) → events
# ---------------------------------------------------------------------------


def _error_event(text: str) -> AgentLoopEvent:
    return AgentLoopEvent("error", text=text)


def translate_claude_code(obj: dict[str, Any], state: dict[str, Any]) -> list[AgentLoopEvent]:
    """Claude Code ``--output-format stream-json`` lines (best effort).

    Shapes per the public stream-json docs: ``system`` init, ``assistant``
    / ``user`` conversation turns with content blocks, and a terminal
    ``result``. The result text duplicates the final assistant text, so it
    is only re-emitted when it differs.
    """
    events: list[AgentLoopEvent] = []
    kind = str(obj.get("type") or "")
    message = obj.get("message")
    blocks = message.get("content") if isinstance(message, dict) else None
    if isinstance(blocks, str):
        blocks = [{"type": "text", "text": blocks}]
    if kind == "assistant" and isinstance(blocks, list):
        for block in blocks:
            if not isinstance(block, dict):
                continue
            block_type = str(block.get("type") or "")
            if block_type == "text":
                text = str(block.get("text") or "")
                if text:
                    state["last_assistant_text"] = text
                    events.append(AgentLoopEvent("content", text=text))
            elif block_type == "thinking":
                events.append(AgentLoopEvent("thinking", text=str(block.get("thinking") or "")))
            elif block_type == "tool_use":
                name = str(block.get("name") or "tool")
                events.append(
                    AgentLoopEvent("tool_call", name=name, data={"args": block.get("input") or {}})
                )
    elif kind == "user" and isinstance(blocks, list):
        for block in blocks:
            if isinstance(block, dict) and str(block.get("type") or "") == "tool_result":
                content = block.get("content")
                text = (
                    content
                    if isinstance(content, str)
                    else json.dumps(content, ensure_ascii=False, default=str)
                )
                events.append(AgentLoopEvent("tool_result", text=text))
    elif kind == "result":
        result_text = str(obj.get("result") or "")
        if result_text and result_text != state.get("last_assistant_text"):
            events.append(AgentLoopEvent("content", text=result_text))
        usage = obj.get("usage")
        data: dict[str, Any] = {}
        if isinstance(usage, dict):
            data["input_tokens"] = usage.get("input_tokens")
            data["output_tokens"] = usage.get("output_tokens")
        if obj.get("total_cost_usd") is not None:
            data["cost_usd"] = obj.get("total_cost_usd")
        if any(value is not None for value in data.values()):
            events.append(AgentLoopEvent("usage", data=data))
        if str(obj.get("subtype") or "") not in {"", "success"}:
            events.append(_error_event(f"claude code turn ended with {obj.get('subtype')!r}"))
    return events


def translate_codex(obj: dict[str, Any], state: dict[str, Any]) -> list[AgentLoopEvent]:
    """Codex CLI ``codex exec --json`` lines (best effort, experimental API).

    The wire format is experimental; unknown ``msg.type`` values are
    skipped so Codex releases that add event kinds degrade to quieter
    traces rather than failed turns.
    """
    events: list[AgentLoopEvent] = []
    msg = obj.get("msg") if isinstance(obj.get("msg"), dict) else obj
    msg_type = str(msg.get("type") or "")
    if msg_type == "agent_message":
        text = str(msg.get("message") or "")
        if text:
            state["last_assistant_text"] = text
            events.append(AgentLoopEvent("content", text=text))
    elif msg_type in {"item.started", "item.completed", "item.updated"}:
        item = msg.get("item") if isinstance(msg.get("item"), dict) else {}
        item_type = str(item.get("type") or "")
        done = msg_type == "item.completed"
        if item_type == "command_execution":
            if done:
                events.append(
                    AgentLoopEvent(
                        "tool_result",
                        name="shell",
                        text=str(item.get("aggregated_output") or item.get("exec_output") or ""),
                        data={"exit_code": item.get("exit_code")},
                    )
                )
            else:
                events.append(
                    AgentLoopEvent("tool_call", name="shell", text=str(item.get("command") or ""))
                )
        elif item_type == "file_change":
            changes = item.get("changes") or []
            names = ",".join(
                str(change.get("path") or "") for change in changes if isinstance(change, dict)
            )
            events.append(
                AgentLoopEvent(
                    "tool_result" if done else "tool_call", name="apply_patch", text=names
                )
            )
        elif item_type == "mcp_tool_call":
            events.append(
                AgentLoopEvent(
                    "tool_result" if done else "tool_call",
                    name=str(item.get("tool") or "mcp"),
                    text=str(item.get("arguments") or item.get("output") or ""),
                )
            )
        elif item_type == "reasoning":
            summary = item.get("summary")
            if (
                isinstance(summary, list)
                and summary
                and isinstance(summary[0], dict)
                and summary[0].get("text")
            ):
                events.append(AgentLoopEvent("thinking", text=str(summary[0].get("text"))))
    elif msg_type == "token_count":
        info = msg.get("info") if isinstance(msg.get("info"), dict) else {}
        total = (
            info.get("total_token_usage") if isinstance(info.get("total_token_usage"), dict) else {}
        )
        last = (
            info.get("last_token_usage") if isinstance(info.get("last_token_usage"), dict) else {}
        )
        data = {
            "input_tokens": total.get("input_tokens"),
            "output_tokens": last.get("output_tokens"),
        }
        if any(value is not None for value in data.values()):
            events.append(AgentLoopEvent("usage", data=data))
    elif msg_type in {"turn_aborted", "error"}:
        events.append(_error_event(str(msg.get("message") or msg_type)))
    elif msg_type == "task_complete":
        text = str(msg.get("last_agent_message") or "")
        if text and text != state.get("last_assistant_text"):
            events.append(AgentLoopEvent("content", text=text))
    return events


def translate_generic(obj: dict[str, Any], state: dict[str, Any]) -> list[AgentLoopEvent]:
    """Heuristic mapping for custom / undocumented CLI JSON lines.

    Recognizes the common ``{"type": …}`` / ``{"kind": …}`` vocabulary and
    the usual text-bearing field names. Anything unrecognized is dropped:
    a custom backend's incidental log lines must not corrupt the answer.
    """
    events: list[AgentLoopEvent] = []
    kind = str(obj.get("type") or obj.get("kind") or obj.get("event") or "").lower()
    if "error" in kind:
        return [
            _error_event(str(obj.get("message") or obj.get("error") or obj.get("text") or kind))
        ]
    if "tool_use" in kind or "tool_call" in kind:
        return [
            AgentLoopEvent(
                "tool_call",
                name=str(obj.get("name") or obj.get("tool") or "tool"),
                data={"args": obj.get("args") or obj.get("input") or obj.get("arguments") or {}},
            )
        ]
    if "tool_result" in kind:
        return [
            AgentLoopEvent(
                "tool_result",
                name=str(obj.get("name") or obj.get("tool") or ""),
                text=str(obj.get("text") or obj.get("result") or obj.get("output") or ""),
            )
        ]
    if "usage" in kind or "token" in kind:
        return [
            AgentLoopEvent(
                "usage",
                data={
                    key: value for key, value in obj.items() if key not in {"type", "kind", "event"}
                },
            )
        ]
    if "think" in kind or "reasoning" in kind:
        return [
            AgentLoopEvent(
                "thinking",
                text=str(obj.get("text") or obj.get("thinking") or obj.get("content") or ""),
            )
        ]
    for field in ("text", "content", "message", "delta", "result", "response"):
        value = obj.get(field)
        if isinstance(value, str) and value:
            state["last_assistant_text"] = value
            return [AgentLoopEvent("content", text=value)]
        if isinstance(value, list):
            # OpenAI-style content block lists.
            for block in value:
                if not isinstance(block, dict):
                    continue
                inner = str(block.get("text") or "")
                if str(block.get("type") or "") == "tool_use" or block.get("name"):
                    events.append(
                        AgentLoopEvent(
                            "tool_call",
                            name=str(block.get("name") or "tool"),
                            data={"args": block.get("input") or {}},
                        )
                    )
                elif inner:
                    events.append(AgentLoopEvent("content", text=inner))
            if events:
                return events
    return events


TRANSLATORS: dict[str, Translator] = {
    "claude-code": translate_claude_code,
    "codex": translate_codex,
    "generic": translate_generic,
}


class CliAgentLoopBackend(AgentLoopBackend):
    """Spawn the configured agent CLI and translate its NDJSON stdout."""

    def __init__(
        self,
        *,
        name: str,
        command: str,
        base_args: list[str],
        extra_args: list[str],
        env: dict[str, str],
        timeout_seconds: float,
        translator: Translator,
    ) -> None:
        self.name = name
        self.command = command
        self.base_args = list(base_args)
        self.extra_args = list(extra_args)
        self.env = {str(key): str(value) for key, value in (env or {}).items()}
        self.timeout_seconds = float(timeout_seconds) if timeout_seconds else 0.0
        self.translator = translator

    def build_argv(self, request: AgentLoopRequest) -> list[str]:
        prompt = request.prompt
        argv = [self.command, *self.base_args]
        if any("{prompt}" in arg for arg in self.extra_args):
            # Custom backends may pin the prompt anywhere in the argv.
            argv.extend(arg.replace("{prompt}", prompt) for arg in self.extra_args)
        else:
            argv.extend(self.extra_args)
            if prompt:
                argv.append(prompt)
        return argv

    async def run(self, request: AgentLoopRequest) -> AsyncIterator[AgentLoopEvent]:
        argv = self.build_argv(request)
        env = {**os.environ, **self.env}
        cwd = request.workdir or None
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                env=env,
            )
        except (OSError, ValueError) as exc:
            raise AgentLoopError(
                t("agent_loop.spawn_failed", backend=self.name, error=str(exc)),
                backend=self.name,
            ) from exc

        stderr_tail: list[bytes] = []
        stderr_task = asyncio.create_task(self._drain_stderr(proc, stderr_tail))
        state: dict[str, Any] = {}
        try:
            # The wall-clock cap covers streaming AND the final wait, so a
            # hung child can never pin a turn open.
            timeout_cm = (
                asyncio.timeout(self.timeout_seconds) if self.timeout_seconds else nullcontext()
            )
            async with timeout_cm:
                async for event in self._iter_stdout(proc, state):
                    yield event
                returncode = await proc.wait()
            if returncode != 0:
                detail = (
                    b"".join(stderr_tail)[-_STDERR_TAIL_LIMIT:].decode("utf-8", "replace").strip()
                )
                raise AgentLoopError(
                    t(
                        "agent_loop.exited",
                        backend=self.name,
                        code=returncode,
                        detail=f": {detail}" if detail else "",
                    ),
                    backend=self.name,
                )
        except TimeoutError as exc:
            raise AgentLoopError(
                t("agent_loop.timeout", backend=self.name, seconds=int(self.timeout_seconds)),
                backend=self.name,
            ) from exc
        finally:
            # Covers turn cancellation mid-stream as well as normal exit.
            if proc.returncode is None:
                with suppress(Exception):
                    proc.kill()
            with suppress(Exception):
                await proc.wait()
            stderr_task.cancel()
            with suppress(Exception):
                await stderr_task

    async def _iter_stdout(
        self, proc: asyncio.subprocess.Process, state: dict[str, Any]
    ) -> AsyncIterator[AgentLoopEvent]:
        if proc.stdout is None:  # pragma: no cover - PIPE always set above
            return
        buffer = bytearray()
        while True:
            chunk = await proc.stdout.read(65536)
            if not chunk:
                break
            buffer.extend(chunk)
            while True:
                newline = buffer.find(b"\n")
                if newline < 0:
                    break
                line = bytes(buffer[:newline])
                del buffer[: newline + 1]
                for event in self._line_events(line, state):
                    yield event
        if buffer.strip():
            for event in self._line_events(bytes(buffer), state):
                yield event

    def _line_events(self, raw: bytes, state: dict[str, Any]) -> list[AgentLoopEvent]:
        text = raw.decode("utf-8", "replace").strip()
        if not text or text.startswith("#") or len(raw) > _MAX_LINE_BYTES:
            if len(raw) > _MAX_LINE_BYTES:
                logger.warning(
                    "agent-loop %s: dropping oversized JSON line (%d bytes)",
                    self.name,
                    len(raw),
                )
            return []
        try:
            obj = json.loads(text)
        except json.JSONDecodeError:
            # Non-JSON stdout (banners, warnings) is trace noise, not errors.
            logger.debug("agent-loop %s: non-JSON stdout line: %.200s", self.name, text)
            return []
        if not isinstance(obj, dict):
            return []
        return self.translator(obj, state)

    @staticmethod
    async def _drain_stderr(proc: asyncio.subprocess.Process, tail: list[bytes]) -> None:
        # A full stderr pipe blocks the child; drain continuously, keep a tail.
        if proc.stderr is None:  # pragma: no cover - PIPE always set above
            return
        try:
            while True:
                chunk = await proc.stderr.read(4096)
                if not chunk:
                    return
                tail.append(chunk)
                total = sum(len(part) for part in tail)
                if total > 4 * _STDERR_TAIL_LIMIT:
                    # Trim from the front, keeping the most recent bytes.
                    excess = total - 2 * _STDERR_TAIL_LIMIT
                    kept: list[bytes] = []
                    for part in reversed(tail):
                        if excess <= 0:
                            kept.append(part)
                            continue
                        if len(part) <= excess:
                            excess -= len(part)
                        else:
                            kept.append(part[excess:])
                            excess = 0
                    tail[:] = list(reversed(kept))
        except Exception:  # pragma: no cover - drain must never propagate
            return
