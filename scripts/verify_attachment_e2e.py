"""One-off E2E verification of the chat attachment chain on the live
detached instance (backlog item: 附件链路人工端到端验证).

Chain under test, entirely against the running backend (127.0.0.1:8082):

  WS start_turn + inline base64 attachment
    -> TurnRequest validation
    -> attachment persisted to the attachment store (record keeps a URL)
    -> materialization into the session's turn workspace
    -> prompt carries the file to the agent backend (Intellect ACP)
    -> user message snapshot persists the attachment record
    -> GET /files/attachments/... serves the original bytes back

Prints PASS/FAIL per checkpoint and exits non-zero on any failure.
"""

from __future__ import annotations

import asyncio
import base64
import json
import pathlib
import sys
import urllib.request

import websockets

WS_URL = "ws://127.0.0.1:8082/ws"
API = "http://127.0.0.1:8082"
PROTOCOL_VERSION = "2.0"
REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]

FILE_NAME = "e2e-attachment-check.txt"
FILE_BODY = "KAGWeb attachment E2E: the magic marker is PINEAPPLE-42."
FILE_B64 = base64.b64encode(FILE_BODY.encode()).decode()

failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  [{detail}]" if detail else ""))
    if not ok:
        failures.append(name)


async def main() -> int:
    session_id = ""
    answer_chunks: list[str] = []
    saw_done = False
    saw_error = ""

    async with websockets.connect(WS_URL, max_size=20 * 1024 * 1024) as ws:
        await ws.send(
            json.dumps(
                {
                    "type": "start_turn",
                    "protocol_version": PROTOCOL_VERSION,
                    "content": (
                        "附件里有一个标记词 PINEAPPLE,后面跟着一个数字。"
                        "请只回复那一行内容,不要做任何其他事情。"
                    ),
                    "capability": "chat",
                    "session_id": None,
                    "language": "zh",
                    "attachments": [
                        {
                            "type": "file",
                            "filename": FILE_NAME,
                            "mime_type": "text/plain",
                            "base64": FILE_B64,
                        }
                    ],
                }
            )
        )

        while True:
            raw = await asyncio.wait_for(ws.recv(), timeout=240)
            event = json.loads(raw)
            etype = str(event.get("type") or "")

            if etype == "session_meta":
                session_id = str(
                    event.get("session_id") or event.get("data", {}).get("session_id") or ""
                )
            elif etype == "content":
                answer_chunks.append(str(event.get("content") or ""))
            elif etype == "error":
                saw_error = str(event.get("content") or event)[:300]
            elif etype == "done":
                session_id = str(event.get("session_id") or session_id)
                saw_done = True
                break

    check("turn completed (done, no error)", saw_done and not saw_error, saw_error)
    check("session id assigned", bool(session_id), session_id)
    answer = "".join(answer_chunks)
    check("agent read the attachment content", "PINEAPPLE-42" in answer, answer[:160])

    # The user message snapshot must persist the attachment record with a
    # durable URL (the store, not the inline base64).
    with urllib.request.urlopen(f"{API}/api/sessions/{session_id}", timeout=15) as response:
        session = json.load(response)
    user_messages = [m for m in session.get("messages", []) if m.get("role") == "user"]
    check("user message persisted", bool(user_messages))
    record = next(
        (
            item
            for message in reversed(user_messages)
            for item in (message.get("attachments") or [])
            if item.get("filename") == FILE_NAME
        ),
        None,
    )
    check("attachment record persisted on the user message", record is not None)
    url = str((record or {}).get("url") or "")
    check("record carries a durable store URL", url.startswith("/files/attachments/"), url)

    # The store must serve the exact original bytes back.
    if url:
        with urllib.request.urlopen(f"{API}{url}", timeout=15) as response:
            served = response.read().decode()
        check("store serves the original bytes", served == FILE_BODY, served[:80])

    # Materialization: the file must exist under the instance's workspace
    # tree — the per-session agent workspace copy is named `<id>_<filename>`
    # (that is the path the CLI-family prompt references).
    hits = [
        path
        for path in (REPO_ROOT / "data" / "user").rglob(f"*{FILE_NAME}")
        if path.name.endswith(FILE_NAME)
    ]
    check(
        "file materialized under the workspace tree",
        bool(hits),
        str(hits[0].relative_to(REPO_ROOT)) if hits else "not found",
    )

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
