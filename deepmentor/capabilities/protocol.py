"""Constants shared by the orchestrator and capability implementations."""

from __future__ import annotations

#: The body to publish as ``agent_output`` on the turn's CAPABILITY_COMPLETE
#: event.
AGENT_OUTPUT = "agent_output"

#: A dict of extras to publish alongside it. Only this sub-dict is forwarded,
#: so a capability states exactly what may leave the turn.
EVENT_METADATA = "event_metadata"

__all__ = ["AGENT_OUTPUT", "EVENT_METADATA"]
