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
        if self.scenario in {"full", "deny"}:
            response = await self.conn.request_permission(
                session_id,
                tool_call={"tool_call_id": "t1", "title": "shell"},
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
        return schema.PromptResponse(
            stop_reason="end_turn",
            usage=schema.Usage(input_tokens=3, output_tokens=5, total_tokens=8),
        )

    async def cancel(self, *args, **kwargs) -> None:
        self.cancelled = True


if __name__ == "__main__":
    asyncio.run(acp.run_agent(FakeAgent()))
