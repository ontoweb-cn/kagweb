"""Message bus module for decoupled channel-agent communication."""

from deepmentor.partners.bus.events import InboundMessage, OutboundMessage
from deepmentor.partners.bus.queue import MessageBus

__all__ = ["MessageBus", "InboundMessage", "OutboundMessage"]
