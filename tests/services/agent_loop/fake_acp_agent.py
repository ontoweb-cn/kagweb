"""Fake ACP agent used by the backend tests.

Runs over stdio via the SDK's own ``run_agent`` so the wire dialect is
exactly what a real ``intellect acp`` speaks. Behavior is scripted by
argv[1]; outcomes are written to the JSON file named by
``FAKE_RESULT_FILE`` so tests can assert what the agent actually saw.
"""

import asyncio
import json
import os
import sys

import acp
import acp.schema as schema


class FakeAgent:
    def __init__(self) -> None:
        self.scenario = sys.argv[1] if len(sys.argv) > 1 else "full"
        self.result_path = os.environ.get("FAKE_RESULT_FILE", "")
        self.conn = None
        self.session_id = "fake-session-1"
        self.reused_session = False
        self.permission_option_id = ""
        self.results: dict = {}

    # -- connection ---------------------------------------------------------

    def on_connect(self, conn) -> None:
        # The SDK calls this synchronously with the AgentSideConnection.
        self.conn = conn

    def _record(self) -> None:
        if not self.result_path:
            return
        with open(self.result_path, "w", encoding="utf-8") as fh:
            json.dump(
                {
                    "permission_option_id": self.permission_option_id,
                    "reused_session": self.reused_session,
                    "scenario": self.scenario,
                },
                fh,
            )

    # -- protocol methods -----------------------------------------------------

    async def initialize(self, *args, **kwargs):
        return schema.InitializeResponse(
            protocol_version=1,
            agent_capabilities=schema.AgentCapabilities(load_session=True),
        )

    async def new_session(self, *args, **kwargs):
        return schema.NewSessionResponse(session_id=self.session_id)

    async def load_session(self, *args, **kwargs):
        if "reject-load" in self.scenario:
            # A session the agent no longer holds: agents drop them on idle
            # expiry or a wiped state DB, and KAGWeb must fall back to
            # new_session instead of failing every later turn.
            raise ValueError("unknown session")
        self.reused_session = True
        return schema.LoadSessionResponse(session_id=self.session_id)

    async def prompt(self, session_id, prompt, **kwargs):
        import traceback

        try:
            return await self._prompt(session_id, prompt, **kwargs)
        except Exception:
            traceback.print_exc()
            raise

    async def _prompt(self, session_id, prompt, **kwargs):
        self.session_id = session_id
        if self.scenario == "hang":
            await asyncio.sleep(30)
            return schema.PromptResponse(stop_reason="end_turn")
        await self.conn.session_update(session_id, acp.update_agent_message_text("Hello "))
        await self.conn.session_update(session_id, acp.update_agent_message_text("world"))
        await self.conn.session_update(
            session_id,
            schema.ToolCallStart(
                tool_call_id="t1",
                title="shell",
                status="in_progress",
                session_update="tool_call",
            ),
        )
        await self.conn.session_update(
            session_id,
            acp.update_tool_call("t1", title="shell", status="completed", raw_output="tool-out"),
        )
        if self.scenario in {"full", "deny", "long-approval"}:
            tool_call = {"tool_call_id": "t1", "title": "shell"}
            if self.scenario == "long-approval":
                # Reproduce Intellect's ``_build_permission_tool_call``: the
                # title carries the whole scan report plus the command, the
                # content block repeats it, and only ``raw_input`` holds the
                # short label. Regression cover for the approval card that
                # used to print a repr()'d transcript twice.
                command = (
                    'cd /tmp && for q in a b; do curl -s "https://example.test/$q" '
                    "| python3 -c 'print(1)'; sleep 3; done"
                )
                description = (
                    "Security scan — [HIGH] Plain HTTP URL in execution context: "
                    "URL 'http://www.w3.org/2005/Atom' uses unencrypted HTTP.\n"
                    "  Safer: use https:// instead."
                )
                tool_call = {
                    "tool_call_id": "t1",
                    "title": f"{description}: {command}",
                    "kind": "execute",
                    "status": "pending",
                    "content": [
                        {
                            "type": "content",
                            "content": {"type": "text", "text": f"{description}\n$ {command}"},
                        }
                    ],
                    "raw_input": {"command": command, "description": description},
                }
            response = await self.conn.request_permission(
                session_id,
                tool_call=tool_call,
                options=[
                    schema.PermissionOption(option_id="opt-allow", name="Allow", kind="allow_once"),
                    schema.PermissionOption(
                        option_id="opt-reject", name="Deny", kind="reject_once"
                    ),
                ],
            )
            outcome = response.outcome
            self.permission_option_id = str(
                getattr(outcome, "option_id", "") or f"outcome:{type(outcome).__name__}"
            )
        self._record()
        # ``<name>-stop-<reason>`` scripts the PromptResponse stop_reason, so a
        # test can drive the truncated / refused paths: ACP allows
        # ``max_tokens`` / ``max_turn_requests`` / ``refusal`` besides the
        # clean ``end_turn``, and an adapter that reports every finish as
        # ``end_turn`` is exactly how a half-finished reply reads as complete.
        stop_reason = "end_turn"
        if "-stop-" in self.scenario:
            stop_reason = self.scenario.rsplit("-stop-", 1)[1]
        return schema.PromptResponse(
            stop_reason=stop_reason,
            usage=schema.Usage(input_tokens=3, output_tokens=5, total_tokens=8),
        )

    async def cancel(self, *args, **kwargs) -> None:
        self.cancelled = True


if __name__ == "__main__":
    asyncio.run(acp.run_agent(FakeAgent()))
