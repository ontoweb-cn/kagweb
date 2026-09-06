"""Default ``chat`` capability — KAGWeb framework shell.

KAGWeb ships as a framework shell: the LLM provider layer, session store,
orchestrator, registries and web front end are all in place, but the
conversation implementation itself is intentionally not wired yet. Every
turn routed here completes normally with a localized notice, so the whole
stack (API, WebSocket, CLI, web UI) stays exercisable while the KAG
backend integration is built on this seam.

Integration point: replace the body of :meth:`ChatCapability.run` (or swap
the class registered in
``deepmentor.runtime.bootstrap.builtin_capabilities``) with the real
implementation.
"""

from __future__ import annotations

from deepmentor.capabilities._shared import emit_capability_result
from deepmentor.core.capability_protocol import CapabilityManifest, TurnCapability
from deepmentor.core.context import UnifiedContext
from deepmentor.services.i18n import t
from deepmentor.services.settings.interface_settings import get_response_language
from deepmentor.services.llm.usage_tracker import UsageTracker


class ChatCapability(TurnCapability):
    """Placeholder turn capability that answers with a shell notice."""

    manifest = CapabilityManifest(
        name="chat",
        description="Default conversation capability (KAGWeb shell: awaiting the KAG backend integration).",
        stages=["responding"],
        tools_used=[],
        cli_aliases=["chat"],
    )

    async def run(self, context: UnifiedContext, stream) -> None:  # noqa: ANN001
        language = get_response_language()
        notice = t(
            "chat.stub_notice",
            language=language,
        )
        await stream.content(notice, source=self.name)
        context.capability_output.agent_output = notice
        context.capability_output.answer_published = True
        await emit_capability_result(
            stream,
            {"response": notice},
            source=self.name,
            usage=UsageTracker(),
        )


__all__ = ["ChatCapability"]
