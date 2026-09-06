"""Group-only collaboration protocol for Partner turns."""

from deepmentor.capabilities.partner_group.capability import PartnerGroupCapability
from deepmentor.capabilities.partner_group.tools import (
    PARTNER_GROUP_TOOL_NAMES,
    PARTNER_GROUP_TOOL_TYPES,
)

__all__ = [
    "PARTNER_GROUP_TOOL_NAMES",
    "PARTNER_GROUP_TOOL_TYPES",
    "PartnerGroupCapability",
]
