"""One-shot Phase 1 patch: context_builder.py BaseAgent replacement."""
import io
import py_compile

path = "deepmentor/services/session/context_builder.py"
with io.open(path, "r", encoding="utf-8") as f:
    src = f.read()


def rep(src, old, new, count=1):
    assert old in src, f"NOT FOUND: {old[:90]!r}"
    return src.replace(old, new, count)


# import: BaseAgent -> services.llm stream
src = rep(
    src,
    """from deepmentor.agents.base_agent import BaseAgent
from deepmentor.core.stream import StreamEvent, StreamEventType""",
    """from deepmentor.core.stream import StreamEvent, StreamEventType""",
)

# drop the _ContextSummaryAgent class
src = rep(
    src,
    '''class _ContextSummaryAgent(BaseAgent):
    """Small helper agent for compressing older conversation turns."""

    def __init__(self, language: str = "en") -> None:
        super().__init__(
            module_name="chat",
            agent_name="context_summary_agent",
            language=language,
        )

    async def process(self, *_args, **_kwargs) -> dict[str, Any]:
        raise NotImplementedError


''',
    "",
)

# replace the trace-bridge + agent invocation with a direct llm stream
src = rep(
    src,
    """        agent = _ContextSummaryAgent(language=language)
        trace_meta = build_trace_metadata(""",
    """        trace_meta = build_trace_metadata(""",
)

src = rep(
    src,
    """        agent.set_trace_callback(_trace_bridge)
        await self._append_event(""",
    """        await self._append_event(""",
)

src = rep(
    src,
    """        try:
            _chunks: list[str] = []
            async for _c in agent.stream_llm(
                user_prompt=user_prompt,
                system_prompt=system_prompt,
                max_tokens=summary_budget,
                stage="summarize_context",
                trace_meta=trace_meta,
            ):
                _chunks.append(_c)
            summary = "".join(_chunks).strip()
            if count_tokens(summary) >= int(summary_budget * TRUNCATION_GUARD_RATIO):
                summary = trim_incomplete_tail(summary)
            return summary, events
        finally:""",
    """        try:
            from deepmentor.services.llm import stream as llm_stream

            await _trace_bridge(
                {"event": "llm_call", "state": "running", **trace_meta}
            )
            _chunks: list[str] = []
            try:
                async for _c in llm_stream(
                    user_prompt,
                    system_prompt=system_prompt,
                    max_tokens=summary_budget,
                ):
                    _chunks.append(_c)
            except Exception as exc:
                await _trace_bridge(
                    {"event": "llm_call", "state": "error", "response": str(exc), **trace_meta}
                )
                raise
            summary = "".join(_chunks).strip()
            await _trace_bridge(
                {"event": "llm_call", "state": "complete", "response": summary, **trace_meta}
            )
            if count_tokens(summary) >= int(summary_budget * TRUNCATION_GUARD_RATIO):
                summary = trim_incomplete_tail(summary)
            return summary, events
        finally:""",
)

with io.open(path, "w", encoding="utf-8", newline="") as f:
    f.write(src)
py_compile.compile(path, doraise=True)
print("context_builder OK")
