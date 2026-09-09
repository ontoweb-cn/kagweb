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

from .protocol import (
    MAX_LINE_BYTES,
    AgentLoopBackend,
    AgentLoopError,
    AgentLoopEvent,
    AgentLoopRequest,
)

logger = logging.getLogger(__name__)

#: Keep stderr tails bounded: they surface in failed-turn error text.
_STDERR_TAIL_LIMIT = 2000

Translator = Callable[[dict[str, Any], dict[str, Any]], list[AgentLoopEvent]]

#: Claude Code's own tool names, mapped to KAGWeb's vocabulary so a CLI-backed
#: turn reads with the same verbs as a native one ("Running command ls …",
#: "Reading skill dataviz") instead of falling back to the vendor's capitalised
#: name. The second element renames the vendor's argument keys, because the
#: UI's chip reads KAGWeb's key (``path``, ``name``) and not the CLI's
#: (``file_path``, ``skill``).
_CLAUDE_CODE_TOOLS: dict[str, tuple[str, dict[str, str]]] = {
    "Bash": ("exec", {}),  # already {command, description, …}
    "Read": ("read_file", {"file_path": "path"}),
    "Write": ("write_file", {"file_path": "path"}),
    "Edit": ("edit_file", {"file_path": "path"}),
    "MultiEdit": ("edit_file", {"file_path": "path"}),
    "NotebookEdit": ("edit_file", {"notebook_path": "path"}),
    "Skill": ("read_skill", {"skill": "name"}),
    "WebFetch": ("web_fetch", {}),
    "WebSearch": ("web_search", {}),
    "Glob": ("glob", {}),
    "Grep": ("grep", {}),
}


def _canonical_tool(name: str, args: Any) -> tuple[str, Any]:
    """``(canonical name, canonical args)`` for one vendor tool call.

    A name this table has not met yet passes through untouched: an unknown
    vendor tool must still appear in the trace, named as the vendor named it.
    """
    entry = _CLAUDE_CODE_TOOLS.get(name)
    if entry is None:
        return name, args
    canonical, key_map = entry
    if not key_map or not isinstance(args, dict):
        return canonical, args
    return canonical, {key_map.get(key, key): value for key, value in args.items()}


#: Environment the agent-loop subprocess may inherit from the server. The
#: server environment carries deployment secrets (settings export writes
#: AUTH_PASSWORD_HASH / POCKETBASE_ADMIN_PASSWORD / provider keys into
#: os.environ), and the child — plus anything it executes — must not read
#: them. Only process-basics and the CLI's own credential locations pass
#: through; backend credentials come exclusively from the operator's
#: ``env`` settings block.
_CHILD_ENV_ALLOWLIST = frozenset(
    {
        # process basics
        "PATH",
        "LANG",
        "LC_ALL",
        "TZ",
        # POSIX / Unix homes and tmp
        "HOME",
        "TMPDIR",
        # Windows: shells, temp dirs, and per-user app data (the agent CLIs
        # keep their own login state under APPDATA/USERPROFILE).
        "SYSTEMROOT",
        "SYSTEMDRIVE",
        "WINDIR",
        "COMSPEC",
        "PATHEXT",
        "TEMP",
        "TMP",
        "USERPROFILE",
        "APPDATA",
        "LOCALAPPDATA",
    }
)


def _build_child_env(os_env: dict[str, str], extra: dict[str, str]) -> dict[str, str]:
    """Allowlisted server env + the operator's explicit ``env`` block."""
    child = {
        key: value for key, value in os_env.items() if key.upper() in _CHILD_ENV_ALLOWLIST and value
    }
    child.update(extra)
    return child


#: Concurrent agent-loop subprocesses per process. Each CLI agent can be
#: heavyweight (node runtime, model context); unbounded parallel turns
#: across sessions would exhaust the host. Per-process by design — each
#: backend worker enforces its own share.
_MAX_CONCURRENT_TURNS = 4
_spawn_slots: asyncio.Semaphore | None = None


def _spawn_semaphore() -> asyncio.Semaphore:
    global _spawn_slots
    if _spawn_slots is None:
        _spawn_slots = asyncio.Semaphore(_MAX_CONCURRENT_TURNS)
    return _spawn_slots


# ---------------------------------------------------------------------------
# Vendor line translators — pure functions, (line_object, run_state) → events
# ---------------------------------------------------------------------------


def _error_event(text: str) -> AgentLoopEvent:
    return AgentLoopEvent("error", text=text)


def _tool_result_text(content: Any) -> str:
    """Flatten a ``tool_result`` payload to text, keeping images out of the event.

    Claude Code can return content as a block list; an image block carries the
    raw base64 in ``source.data``, which would otherwise be inlined verbatim
    (megabytes per screenshot, and close to the per-line cap). Images are
    summarised instead — the stream has no image event to carry them.
    """
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return "" if content is None else json.dumps(content, ensure_ascii=False, default=str)
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        block_type = str(block.get("type") or "")
        if block_type == "text":
            parts.append(str(block.get("text") or ""))
        elif block_type == "image":
            source = block.get("source") if isinstance(block.get("source"), dict) else {}
            data = source.get("data")
            parts.append(
                f"[image {source.get('media_type') or 'unknown'}, "
                f"{len(data) if isinstance(data, str) else 0} base64 chars]"
            )
        else:
            parts.append(json.dumps(block, ensure_ascii=False, default=str))
    return "\n".join(part for part in parts if part)


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
                name, args = _canonical_tool(
                    str(block.get("name") or "tool"), block.get("input") or {}
                )
                tool_id = str(block.get("id") or "")
                if tool_id:
                    # Remembered so the matching tool_result can name its tool:
                    # the result block carries only the id, not the name.
                    state.setdefault("tool_names", {})[tool_id] = name
                events.append(
                    AgentLoopEvent(
                        "tool_call",
                        name=name,
                        data={"args": args, "id": tool_id},
                    )
                )
    elif kind == "user" and isinstance(blocks, list):
        for block in blocks:
            if isinstance(block, dict) and str(block.get("type") or "") == "tool_result":
                tool_id = str(block.get("tool_use_id") or "")
                events.append(
                    AgentLoopEvent(
                        "tool_result",
                        name=str((state.get("tool_names") or {}).get(tool_id) or ""),
                        text=_tool_result_text(block.get("content")),
                        data={"id": tool_id, "is_error": bool(block.get("is_error"))},
                    )
                )
    elif kind == "system":
        # The init line names what actually ran; without it the trace cannot
        # say which model or permission mode produced the turn.
        if str(obj.get("subtype") or "") == "init":
            model = str(obj.get("model") or "")
            permission = str(obj.get("permissionMode") or "")
            tools = obj.get("tools") if isinstance(obj.get("tools"), list) else []
            bits = [
                bit
                for bit in (
                    f"model={model}" if model else "",
                    f"permission={permission}" if permission else "",
                    f"tools={len(tools)}" if tools else "",
                )
                if bit
            ]
            if bits:
                events.append(AgentLoopEvent("progress", text=" · ".join(bits)))
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
            # The result line's usage describes this whole CLI invocation, so
            # the counters are coherent as a pass total. Stamped only here:
            # an always-present scope key would defeat the guard above and
            # emit a usage event with nothing in it.
            data["usage_scope"] = "pass"
            events.append(AgentLoopEvent("usage", data=data))
        if str(obj.get("subtype") or "") not in {"", "success"}:
            events.append(_error_event(f"claude code turn ended with {obj.get('subtype')!r}"))
    return events


def _codex_failed(item: dict[str, Any]) -> bool:
    """Whether a completed Codex item reports failure.

    Codex says so two ways — an item status, or a non-zero exit code on a
    command. The trace row reads ``is_error``, and a failed command rendered as
    a clean finish is worse than a false alarm.
    """
    if str(item.get("status") or "").lower() == "failed":
        return True
    exit_code = item.get("exit_code")
    return isinstance(exit_code, int) and not isinstance(exit_code, bool) and exit_code != 0


def _codex_mcp_output(item: dict[str, Any]) -> str:
    """The text an MCP item returned.

    The result payload is not the item's ``arguments`` (those are the request);
    Codex reports output either directly or as a content-block list.
    """
    output = item.get("output")
    if isinstance(output, str) and output:
        return output
    result = item.get("result")
    if isinstance(result, dict) and isinstance(result.get("content"), list):
        parts = [
            str(block.get("text") or "")
            for block in result["content"]
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        return "\n".join(part for part in parts if part)
    return str(output or "")


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
        # ``item.updated`` is progress on an item that already opened, not a new
        # call: emitting one for it would render duplicate rows that never
        # receive a result.
        opening = msg_type == "item.started"
        item_id = str(item.get("id") or "")
        if item_type == "command_execution":
            command = str(item.get("command") or "")
            if done:
                events.append(
                    AgentLoopEvent(
                        "tool_result",
                        name="exec",
                        text=str(item.get("aggregated_output") or item.get("exec_output") or ""),
                        data={
                            "id": item_id,
                            "exit_code": item.get("exit_code"),
                            "is_error": _codex_failed(item),
                        },
                    )
                )
            elif opening:
                # The command rides in ``args`` as well as ``text``: the trace
                # row's chip reads KAGWeb's ``exec`` argument, and the text is
                # kept for consumers that only look at the event body.
                events.append(
                    AgentLoopEvent(
                        "tool_call",
                        name="exec",
                        text=command,
                        data={"id": item_id, "args": {"command": command}},
                    )
                )
        elif item_type == "file_change":
            changes = item.get("changes") or []
            names = ",".join(
                str(change.get("path") or "") for change in changes if isinstance(change, dict)
            )
            if done:
                events.append(
                    AgentLoopEvent(
                        "tool_result",
                        name="edit_file",
                        text=names,
                        data={"id": item_id, "is_error": _codex_failed(item)},
                    )
                )
            elif opening:
                events.append(
                    AgentLoopEvent(
                        "tool_call",
                        name="edit_file",
                        text=names,
                        data={"id": item_id, "args": {"path": names}},
                    )
                )
        elif item_type == "mcp_tool_call":
            name = str(item.get("tool") or "mcp")
            if done:
                events.append(
                    AgentLoopEvent(
                        "tool_result",
                        name=name,
                        text=_codex_mcp_output(item),
                        data={"id": item_id, "is_error": _codex_failed(item)},
                    )
                )
            elif opening:
                events.append(
                    AgentLoopEvent(
                        "tool_call",
                        name=name,
                        text=str(item.get("arguments") or ""),
                        data={"id": item_id, "args": item.get("arguments") or {}},
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
            # input is a running total while output is the last message's, so
            # the pair is not a coherent per-pass figure — say so instead of
            # letting a reader treat it as one. Stamped only when a counter
            # exists: an always-present scope key would defeat the guard.
            data["usage_scope"] = "cumulative"
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

    uses_workdir = True

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
        text_output: bool = False,
    ) -> None:
        self.name = name
        self.command = command
        self.base_args = list(base_args)
        self.extra_args = list(extra_args)
        self.env = {str(key): str(value) for key, value in (env or {}).items()}
        self.timeout_seconds = float(timeout_seconds) if timeout_seconds else 0.0
        self.translator = translator
        # Text-output mode: stdout is the final answer as plain text (no
        # NDJSON, no progress events). The whole stream becomes exactly one
        # content block once the child exits cleanly.
        self.text_output = text_output

    @staticmethod
    def _prompt_with_history(request: AgentLoopRequest) -> str:
        """Fold the transcript into the prompt.

        A CLI is spawned fresh for every turn and argv is its only input
        channel, so the prior conversation has to travel with the prompt —
        unlike the HTTP family, which POSTs ``history`` as its own field. The
        caller already budgets the transcript (ContextBuilder keeps it inside
        a rolling-summary budget), so this cannot grow without bound.
        """
        labels = {"user": "User", "assistant": "Assistant", "system": "System"}
        lines: list[str] = []
        for item in request.history or []:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role") or "").strip().lower()
            content = str(item.get("content") or "").strip()
            # Unknown roles are dropped rather than mislabelled: only the
            # three roles a conversation actually has are named.
            if role not in labels or not content:
                continue
            lines.append(f"{labels[role]}: {content}")
        if not lines:
            return request.prompt
        transcript = "\n\n".join(lines)
        return f"Conversation so far:\n\n{transcript}\n\n{request.prompt}"

    def build_argv(self, request: AgentLoopRequest) -> list[str]:
        prompt = self._prompt_with_history(request)
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
        env = _build_child_env(dict(os.environ), self.env)
        cwd = request.workdir or None
        proc: asyncio.subprocess.Process | None = None
        stderr_task: asyncio.Task[None] | None = None
        stderr_tail: list[bytes] = []
        state: dict[str, Any] = {"dropped_lines": 0}
        try:
            # The wall-clock cap covers slot waiting, spawn, streaming AND
            # the final wait, so a hung child (or a saturated slot pool)
            # can never pin a turn open.
            timeout_cm = (
                asyncio.timeout(self.timeout_seconds) if self.timeout_seconds else nullcontext()
            )
            async with timeout_cm, _spawn_semaphore():
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
                stderr_task = asyncio.create_task(self._drain_stderr(proc, stderr_tail))
                if self.text_output:
                    answer = await self._collect_stdout(proc)
                else:
                    async for event in self._iter_stdout(proc, state):
                        yield event
                    answer = ""
                returncode = await proc.wait()
            if self.text_output and returncode == 0 and answer.strip():
                yield AgentLoopEvent("content", text=answer)
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
            if proc is not None:
                if proc.returncode is None:
                    with suppress(Exception):
                        proc.kill()
                with suppress(Exception):
                    await proc.wait()
            if stderr_task is not None:
                stderr_task.cancel()
                with suppress(Exception):
                    await stderr_task
            dropped = int(state.get("dropped_lines") or 0)
            if dropped:
                logger.info(
                    "agent-loop %s: dropped %d unparseable stdout line(s)", self.name, dropped
                )

    async def _collect_stdout(self, proc: asyncio.subprocess.Process) -> str:
        """Read the child's whole stdout as the final answer text.

        Bounded like a single NDJSON line: a runaway stream is truncated at
        MAX_LINE_BYTES so a chatty child cannot exhaust memory.
        """
        if proc.stdout is None:  # pragma: no cover - PIPE always set above
            return ""
        chunks: list[bytes] = []
        total = 0
        truncated = False
        while True:
            chunk = await proc.stdout.read(65536)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_LINE_BYTES:
                truncated = True
                break
            chunks.append(chunk)
        if truncated:
            logger.warning(
                "agent-loop %s: text output exceeded %d bytes, truncating",
                self.name,
                MAX_LINE_BYTES,
            )
        return b"".join(chunks).decode("utf-8", "replace")

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
        if not text or text.startswith("#"):
            return []
        if len(raw) > MAX_LINE_BYTES:
            logger.warning(
                "agent-loop %s: dropping oversized JSON line (%d bytes)",
                self.name,
                len(raw),
            )
            state["dropped_lines"] = int(state.get("dropped_lines") or 0) + 1
            return []
        try:
            obj = json.loads(text)
        except json.JSONDecodeError:
            # Non-JSON stdout (banners, warnings) is trace noise, not errors,
            # but stays countable so a chatty backend is visible in the logs.
            logger.debug("agent-loop %s: non-JSON stdout line: %.200s", self.name, text)
            state["dropped_lines"] = int(state.get("dropped_lines") or 0) + 1
            return []
        if not isinstance(obj, dict):
            state["dropped_lines"] = int(state.get("dropped_lines") or 0) + 1
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
