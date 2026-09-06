"""Setup loop capability — DeepMentor inspecting and changing its own configuration."""

from deepmentor.capabilities.setup.capability import SetupCapability
from deepmentor.capabilities.setup.tools import SETUP_TOOL_NAMES, SETUP_TOOL_TYPES

__all__ = ["SETUP_TOOL_NAMES", "SETUP_TOOL_TYPES", "SetupCapability"]
