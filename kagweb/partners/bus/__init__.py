"""Message bus module for decoupled channel-agent communication."""

from kagweb.partners.bus.events import InboundMessage, OutboundMessage
from kagweb.partners.bus.queue import MessageBus

__all__ = ["MessageBus", "InboundMessage", "OutboundMessage"]
