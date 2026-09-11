"""Core contracts shared across runtime and capabilities."""

from .capability_protocol import CapabilityManifest, StreamBusProtocol, TurnCapability
from .context import Attachment, UnifiedContext
from .stream import StreamEvent, StreamEventType
from .trace import build_trace_metadata, merge_trace_metadata, new_call_id

__all__ = [
    "StreamEvent",
    "StreamEventType",
    "StreamBusProtocol",
    "new_call_id",
    "build_trace_metadata",
    "merge_trace_metadata",
    "TurnCapability",
    "CapabilityManifest",
    "UnifiedContext",
    "Attachment",
]


def __getattr__(name: str):
    if name == "BaseCapability":
        from . import capability_protocol

        return capability_protocol.__getattr__(name)
    raise AttributeError(name)
