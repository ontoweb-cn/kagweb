"""KB seed retrieval must be visible in the session activity trace."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from deepmentor.agents.chat.agentic_pipeline import AgenticChatPipeline
from deepmentor.core.context import UnifiedContext
from deepmentor.runtime.stream_bus import StreamBus


def test_kb_seed_emits_tool_call_and_graph_result(monkeypatch) -> None:
    monkeypatch.setattr(
        "deepmentor.agents.chat.agentic_pipeline.get_llm_config",
        lambda: SimpleNamespace(
            binding="openai", model="gpt-test", api_key="k", base_url="u", api_version=None
        ),
    )
    pipe = AgenticChatPipeline(language="en")
    monkeypatch.setattr(pipe, "_coexisting_rag_kbs", lambda _context: ["test1"])

    graph = {
        "nodes": [{"id": "a", "label": "A"}, {"id": "b", "label": "B"}],
        "edges": [{"source": "a", "target": "b", "weight": 1}],
    }

    async def execute(_tool, args, *, stream, retrieve_meta):
        assert args == {"query": "q", "kb_name": "test1", "mode": "hybrid"}
        assert retrieve_meta is not None
        await stream.progress("running", metadata={"call_kind": "rag_retrieval"})
        return {
            "result_text": "grounded context",
            "success": True,
            "sources": [{"title": "source.pdf"}],
            "metadata": {
                "content": "grounded context",
                "provider": "graphrag",
                "graph": graph,
            },
        }

    monkeypatch.setattr(pipe, "_execute_tool_call", execute)
    bus = StreamBus()
    context = UnifiedContext(user_message="q", knowledge_bases=["test1"])
    result = asyncio.run(pipe._retrieve_kb_seed_block(context, bus))

    assert "grounded context" in result
    assert [event.type.value for event in bus._history[:2]] == ["tool_call", "progress"]
    tool_result = next(event for event in bus._history if event.type.value == "tool_result")
    assert tool_result.type.value == "tool_result"
    assert tool_result.content == "grounded context"
    assert tool_result.metadata["sources"] == [{"title": "source.pdf"}]
    assert tool_result.metadata["tool_metadata"]["provider"] == "graphrag"
    assert tool_result.metadata["tool_metadata"]["graph"] == graph
