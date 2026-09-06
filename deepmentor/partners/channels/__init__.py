"""Chat channels module with plugin architecture."""

from deepmentor.partners.channels.base import BaseChannel
from deepmentor.partners.channels.manager import ChannelManager

__all__ = ["BaseChannel", "ChannelManager"]
