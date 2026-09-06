"""Chat channels module with plugin architecture."""

from kagweb.partners.channels.base import BaseChannel
from kagweb.partners.channels.manager import ChannelManager

__all__ = ["BaseChannel", "ChannelManager"]
