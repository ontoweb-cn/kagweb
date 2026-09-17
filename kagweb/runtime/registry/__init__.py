"""Runtime registry for capabilities, exported lazily.

Only capabilities: the tool registry went with the tool layer, and leaving a
lazy export behind for it turned a clear "this module is gone" into an
import-time surprise at first attribute access.
"""

from __future__ import annotations

import importlib

_EXPORTS = {
    "CapabilityRegistry": (".capability_registry", "CapabilityRegistry"),
    "get_capability_registry": (".capability_registry", "get_capability_registry"),
}

__all__ = [
    "CapabilityRegistry",
    "get_capability_registry",
]


def __getattr__(name: str):
    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attr_name = target
    value = getattr(importlib.import_module(module_name, __name__), attr_name)
    globals()[name] = value
    return value
