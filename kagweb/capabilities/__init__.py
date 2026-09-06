"""Built-in turn capabilities.

KAGWeb ships a single built-in capability: ``chat`` (currently a shell stub
awaiting the KAG backend integration). Additional capabilities register
through the capability registry at runtime.
"""

from kagweb.capabilities._shared import emit_capability_result
from kagweb.capabilities.protocol import AGENT_OUTPUT, EVENT_METADATA

__all__ = [
    "AGENT_OUTPUT",
    "EVENT_METADATA",
    "emit_capability_result",
]
