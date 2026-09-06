"""Import-free descriptors for built-in turn capabilities."""

from __future__ import annotations

from dataclasses import dataclass

from deepmentor.core.capability_protocol import CapabilityManifest


@dataclass(frozen=True, slots=True)
class BuiltinCapabilitySpec:
    class_path: str
    manifest: CapabilityManifest


def _manifest(
    name: str,
    description: str,
    *,
    stages: list[str],
    tools_used: list[str],
    cli_aliases: list[str],
    config_defaults: dict[str, object] | None = None,
) -> CapabilityManifest:
    return CapabilityManifest(
        name=name,
        description=description,
        stages=stages,
        tools_used=tools_used,
        cli_aliases=cli_aliases,
        config_defaults=config_defaults or {},
    )


BUILTIN_CAPABILITY_CLASSES: dict[str, str] = {
    "chat": "deepmentor.capabilities.chat.capability:ChatCapability",
}


BUILTIN_CAPABILITY_SPECS: dict[str, BuiltinCapabilitySpec] = {
    "chat": BuiltinCapabilitySpec(
        BUILTIN_CAPABILITY_CLASSES["chat"],
        _manifest(
            "chat",
            "Default conversation capability (KAGWeb shell: awaiting the KAG backend integration).",
            stages=["responding"],
            tools_used=[],
            cli_aliases=["chat"],
        ),
    ),
}


__all__ = [
    "BUILTIN_CAPABILITY_CLASSES",
    "BUILTIN_CAPABILITY_SPECS",
    "BuiltinCapabilitySpec",
]
