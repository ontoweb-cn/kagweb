"""Built-in turn capabilities.

KAGWeb ships a single built-in capability: ``chat`` — a shell stub until an
agent-loop backend is configured (see ``kagweb/services/agent_loop/``).
Additional capabilities register through the capability registry at runtime.
"""

from kagweb.capabilities._shared import emit_capability_result
from kagweb.capabilities.protocol import AGENT_OUTPUT, EVENT_METADATA


def capability_tool_owners() -> dict[str, str]:
    """Map built-in tool name -> the capability that declares it.

    The settings page groups tool cards by their owning capability. Derived
    from each registered manifest's ``tools_used`` (first capability wins),
    so a newly registered capability carrying tool declarations shows up
    without further changes here.
    """
    from kagweb.runtime.registry.capability_registry import get_capability_registry

    try:
        manifests = get_capability_registry().get_manifests()
    except Exception:
        return {}
    owners: dict[str, str] = {}
    for manifest in manifests:
        capability_name = str(manifest.get("name") or "")
        for tool in manifest.get("tools_used") or []:
            tool_name = str(tool)
            if tool_name and tool_name not in owners:
                owners[tool_name] = capability_name
    return owners


__all__ = [
    "AGENT_OUTPUT",
    "EVENT_METADATA",
    "capability_tool_owners",
    "emit_capability_result",
]
