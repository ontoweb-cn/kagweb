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
        self.applied_model = ""
        self.results: dict = {}

    # -- connection ---------------------------------------------------------

    def on_connect(self, conn) -> None:
        # The SDK calls this synchronously with the AgentSideConnection.
        self.conn = conn

    def _model_config_options(self, current: str = "") -> list:
        # Reproduce Intellect's model selector (acp_adapter/server.py
        # `_build_model_config_options`): a select config option whose id is
        # "model", with a current value and flat options. Only advertised when
        # the scenario names "models".
        if "models" not in self.scenario:
            return []
        current = current or "deepseek:deepseek-flash"
        return [
            schema.SessionConfigOptionSelect(
                type="select",
                id="model",
                name="Model",
                description="The model used for this session.",
                current_value=current,
                options=[
                    schema.SessionConfigSelectOption(
                        value="deepseek:deepseek-flash", name="deepseek-flash"
                    ),
                    schema.SessionConfigSelectOption(
                        value="deepseek:deepseek-v4-pro",
                        name="deepseek-v4-pro",
                        description="Bigger context",
                    ),
                ],
            )
        ]

    def _record(self) -> None:
        if not self.result_path:
            return
        with open(self.result_path, "w", encoding="utf-8") as fh:
            json.dump(
                {
                    "permission_option_id": self.permission_option_id,
                    "reused_session": self.reused_session,
                    "scenario": self.scenario,
                    "elicitation_action": getattr(self, "elicitation_action", ""),
                    "elicitation_answer": getattr(self, "elicitation_answer", ""),
                    "applied_model": self.applied_model,
                    "prompt_head": getattr(self, "prompt_head", ""),
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
        return schema.NewSessionResponse(
            session_id=self.session_id, config_options=self._model_config_options()
        )

    async def load_session(self, *args, **kwargs):
        if "reject-load" in self.scenario:
            # A session the agent no longer holds: agents drop them on idle
            # expiry or a wiped state DB, and KAGWeb must fall back to
            # new_session instead of failing every later turn.
            raise ValueError("unknown session")
        self.reused_session = True
        return schema.LoadSessionResponse(
            session_id=self.session_id, config_options=self._model_config_options()
        )

    async def set_config_option(self, config_id: str, session_id: str, value: str, **kwargs):
        if str(config_id) == "model":
            self.applied_model = str(value or "")
        return schema.SetSessionConfigOptionResponse(
            config_options=self._model_config_options(current=self.applied_model)
        )

    async def prompt(self, session_id, prompt, **kwargs):
        import traceback

        try:
            return await self._prompt(session_id, prompt, **kwargs)
        except Exception:
            traceback.print_exc()
            raise

    async def _prompt(self, session_id, prompt, **kwargs):
        self.session_id = session_id
        # Head of the first prompt block, for the reset-fold assertion (G-1).
        try:
            blocks = prompt if isinstance(prompt, list) else [prompt]
            self.prompt_head = str(getattr(blocks[0], "text", "") or "")[:400] if blocks else ""
        except Exception:
            self.prompt_head = ""
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
        if "elicitation" in self.scenario:
            # Reproduce Intellect's clarify: a *form* elicitation carrying a
            # single ``answer`` property, whose title is the question and whose
            # enum is the suggested choices (acp_adapter/clarify.py).
            from acp.schema import (
                ElicitationFormSessionMode,
                ElicitationSchema,
                ElicitationStringPropertySchema,
            )

            mode = ElicitationFormSessionMode(
                session_id=session_id,
                requested_schema=ElicitationSchema(
                    type="object",
                    properties={
                        "answer": ElicitationStringPropertySchema(
                            type="string",
                            title="Which database should I target?",
                            enum=["postgres", "sqlite"],
                        )
                    },
                    required=["answer"],
                ),
            )
            response = await self.conn.create_elicitation(
                message="The agent needs more information to continue.",
                mode=mode,
            )
            self.elicitation_action = str(getattr(response, "action", "") or "")
            content = getattr(response, "content", None)
            self.elicitation_answer = (
                str(content.get("answer") or "") if isinstance(content, dict) else ""
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
