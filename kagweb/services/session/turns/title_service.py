"""Generated behavior slice of the unified turn runtime."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any

from kagweb.core.stream import StreamEvent, StreamEventType

from .._turn_runtime_shared import (
    _clip_text,
    _looks_like_error_payload,
    _sanitize_session_title,
    _TurnExecution,
)

logger = logging.getLogger(__name__)

#: The judge's vocabulary. One tuple feeds both the prompt and the validation —
#: the frontend mirrors it in `web/lib/turn-insight.ts` (`INSIGHT_TYPES`).
_INSIGHT_TYPES = ("insight", "ruleout", "decision", "pivot", "open")

#: The judge must answer inside the client's post-DONE socket hold
#: (``POST_DONE_DISCONNECT_DELAY_MS`` = 15s) or the live badge is lost.
_INSIGHT_TIMEOUT_S = 12.0

if TYPE_CHECKING:
    from kagweb.services.session.protocol import SessionStoreProtocol


class SessionTitleService:
    if TYPE_CHECKING:
        store: SessionStoreProtocol

        async def _publish_live_event(
            self,
            execution: _TurnExecution,
            event: StreamEvent,
        ) -> dict[str, Any]: ...

    async def _maybe_generate_session_title(
        self,
        *,
        execution: _TurnExecution,
        session_id: str,
        ui_language: str,
    ) -> None:
        """Generate a short LLM-written title for a freshly-named session.

        Runs only when the session still carries the ``New conversation``
        sentinel — once a user manually renames the chat (or this method
        has already filled in a title), it short-circuits. Runs on the task
        model when one is configured, and otherwise on the LLM scope already
        active on the calling task, which is the user's currently selected
        model.
        """
        if not session_id:
            return
        session = await self.store.get_session(session_id)
        if not session:
            return
        current_title = str(session.get("title") or "").strip()
        if current_title and current_title != "New conversation":
            return

        messages = await self.store.get_messages(session_id)
        first_user = ""
        first_assistant = ""
        for m in messages:
            role = str(m.get("role") or "")
            content = str(m.get("content") or "").strip()
            if not content:
                continue
            if role == "user" and not first_user:
                first_user = content
            elif role == "assistant" and not first_assistant:
                first_assistant = content
            if first_user and first_assistant:
                break
        if not first_user or not first_assistant:
            return

        # Bare deployments (no model at all) skip straight to the truncation
        # fallback instead of paying an LLMConfigError round-trip per turn.
        # Broken-but-present configs still attempt the call so their real
        # error surfaces (and the existing handlers degrade gracefully).
        from kagweb.services.llm.config import has_configured_llm

        title = ""
        if has_configured_llm():
            try:
                from kagweb.services.llm import stream as llm_stream

                zh = str(ui_language or "").lower().startswith("zh")
                if zh:
                    sys_prompt = (
                        "你需要为一段对话生成一个简洁的标题。"
                        "直接输出标题文本，不要引号、不要 Markdown 格式、"
                        '不要末尾标点、不要 "标题：" 这类前缀。'
                        "标题控制在 4-10 个汉字以内。"
                    )
                    user_prompt = (
                        "请基于以下对话生成标题：\n\n"
                        f"[用户]\n{_clip_text(first_user, 800)}\n\n"
                        f"[助手]\n{_clip_text(first_assistant, 1500)}"
                    )
                else:
                    sys_prompt = (
                        "You generate a concise, descriptive title for a "
                        "conversation. Output only the title as plain text "
                        "— no quotes, no markdown, no trailing punctuation, "
                        'no "Title:" prefix. Keep it 4-8 words.'
                    )
                    user_prompt = (
                        "Generate a title for this conversation:\n\n"
                        f"[User]\n{_clip_text(first_user, 800)}\n\n"
                        f"[Assistant]\n{_clip_text(first_assistant, 1500)}"
                    )

                async def _collect_title() -> str:
                    buf: list[str] = []
                    async for c in llm_stream(
                        prompt=user_prompt,
                        system_prompt=sys_prompt,
                        temperature=0.3,
                        max_tokens=80,
                    ):
                        buf.append(c)
                    return "".join(buf)

                from kagweb.services.model_selection.tasks import task_llm_scope

                # The scope is entered before the task is created so `wait_for`'s
                # inner task copies it; with no task model configured it is a no-op.
                with task_llm_scope():
                    raw_title = await asyncio.wait_for(_collect_title(), timeout=20.0)
                if _looks_like_error_payload(raw_title):
                    logger.debug("Title model streamed an error payload — falling back")
                    raw_title = ""
                title = _sanitize_session_title(raw_title)
            except asyncio.TimeoutError:
                logger.debug("Title LLM call timed out — falling back")
            except Exception:
                logger.debug("Title LLM call failed", exc_info=True)

        if not title:
            # Fallback: truncate the first user message so the sidebar
            # doesn't sit on "New conversation" indefinitely when the
            # title model errors out.
            title = first_user[:50] + ("..." if len(first_user) > 50 else "")

        if not title:
            return

        try:
            await self.store.update_session_title(session_id, title)
        except Exception:
            # Not debug: the conversation keeps its placeholder title forever
            # and nothing else reports it. A silent failure here is how the
            # sidebar ends up permanently wrong.
            logger.warning(
                "Could not store generated title for session %s", session_id, exc_info=True
            )
            return

        await self._publish_live_event(
            execution,
            StreamEvent(
                type=StreamEventType.SESSION_META,
                source="turn_runtime",
                stage="title",
                content=title,
                metadata={"title": title, "session_id": session_id},
            ),
        )

    async def _maybe_generate_turn_insight(
        self,
        *,
        execution: _TurnExecution,
        session_id: str,
        turn_id: str,
        assistant_message_id: int | str | None,
        ui_language: str,
        question: str,
        answer: str,
        tool_names: list[str],
        round_count: int,
    ) -> None:
        """One cheap judge call per multi-round turn: a one-line takeaway and
        an epistemic type (insight / ruleout / decision / pivot / open).

        Display-only metadata — never enters context. Any failure is silent
        (debug log): the insight must never delay or break the turn's own
        completion. Single-round turns are skipped — the badge marks how a
        multi-round exploration *moved*, and a plain Q&A did not move.
        """
        if not assistant_message_id or round_count < 2:
            return
        if not question.strip() or not answer.strip():
            return

        from kagweb.services.llm.config import has_configured_llm
        from kagweb.services.settings.interface_settings import get_turn_insight_enabled

        if not get_turn_insight_enabled() or not has_configured_llm():
            return

        types = ", ".join(_INSIGHT_TYPES)
        zh = str(ui_language or "").lower().startswith("zh")
        if zh:
            sys_prompt = (
                "你是探索过程标注器。阅读一轮多步探索（用户问题、助手最终回答与使用的"
                '工具），输出严格 JSON：{"takeaway": "…", "type": "…"}。takeaway 是'
                f"这轮探索的一句收获（不超过 20 个汉字）；type 从 [{types}] 中选一个："
                "insight=新洞见，ruleout=排除了某条路，decision=做出决策，pivot=转向，"
                "open=留下开放问题。只输出 JSON。"
            )
        else:
            sys_prompt = (
                "You annotate one multi-round exploration (the user's question, the "
                "assistant's final answer, and the tools used). Output strict JSON: "
                '{"takeaway": "…", "type": "…"}. takeaway is what the exploration '
                "concluded, in one line (max 12 words); type is one of "
                f"[{types}]: insight=new understanding, ruleout=ruled a path out, "
                "decision=made a decision, pivot=changed direction, open=left a "
                "question open. Output only the JSON."
            )
        tools_block = "\n".join(f"- {name}" for name in tool_names[:12]) or "-"
        user_prompt = (
            f"[Question]\n{_clip_text(question, 800)}\n\n"
            f"[Tools used]\n{tools_block}\n\n"
            f"[Rounds]\n{round_count}\n\n"
            f"[Final answer]\n{_clip_text(answer, 1500)}"
        )

        try:

            async def _collect_insight() -> str:
                buf: list[str] = []
                from kagweb.services.llm import stream as llm_stream

                async for chunk in llm_stream(
                    prompt=user_prompt,
                    system_prompt=sys_prompt,
                    temperature=0.2,
                    max_tokens=120,
                ):
                    buf.append(chunk)
                return "".join(buf)

            from kagweb.services.model_selection.tasks import task_llm_scope

            # The scope is entered before the task is created so `wait_for`'s
            # inner task copies it; with no task model configured it is a no-op.
            with task_llm_scope():
                raw = await asyncio.wait_for(_collect_insight(), timeout=_INSIGHT_TIMEOUT_S)
            # No `_looks_like_error_payload` here: it rejects anything starting
            # with `{` (right for a title, fatal for a judge whose prompt asks
            # for bare JSON). A provider error payload either fails the parse
            # or carries no `takeaway`, which the checks below already catch.
            cleaned = raw.strip()
            if cleaned.startswith("```"):
                cleaned = cleaned.strip("`")
                if cleaned.startswith("json"):
                    cleaned = cleaned[4:]
            parsed = json.loads(cleaned.strip())
            if not isinstance(parsed, dict):
                return
            takeaway = str(parsed.get("takeaway") or "").strip()
            insight_type = str(parsed.get("type") or "").strip().lower()
            if not takeaway:
                return
            if insight_type not in _INSIGHT_TYPES:
                insight_type = "insight"
        except asyncio.TimeoutError:
            logger.debug("Turn insight LLM call timed out — skipping")
            return
        except Exception:
            logger.debug("Turn insight generation failed — skipping", exc_info=True)
            return

        insight = {"takeaway": takeaway[:60], "type": insight_type}
        try:
            stored = await self.store.update_message_metadata(
                assistant_message_id, {"turn_insight": insight}
            )
        except Exception:
            logger.warning(
                "Could not store turn insight for message %s",
                assistant_message_id,
                exc_info=True,
            )
            return
        if not stored:
            # The badge would vanish on the next reload; do not show one that
            # the persisted session cannot reproduce.
            logger.warning("Turn insight was not stored for message %s", assistant_message_id)
            return

        await self._publish_live_event(
            execution,
            StreamEvent(
                type=StreamEventType.PROGRESS,
                source="turn_runtime",
                stage="insight",
                content="",
                metadata={
                    "trace_kind": "turn_insight",
                    "turn_id": turn_id,
                    "session_id": session_id,
                    # The client matches the badge to this row; "last assistant
                    # message" would land on the next turn's bubble when the
                    # user sends again before this event arrives.
                    "assistant_message_id": assistant_message_id,
                    "takeaway": insight["takeaway"],
                    "insight_type": insight_type,
                },
            ),
        )
