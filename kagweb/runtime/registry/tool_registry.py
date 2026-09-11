"""
Tool Registry
=============

Central registry that manages externally-provided tools (MCP servers, CLI
apps, plugins). Provides lookup, listing, and OpenAI schema generation.
Built-in prompt-time tools are gone; the registry stays as the dispatch
point every provider tool syncs into.
"""

from __future__ import annotations

import logging
from typing import Any

from kagweb.core.tool_protocol import BaseTool, ToolDefinition

logger = logging.getLogger(__name__)


class ToolRegistry:
    """
    Process-wide registry of available provider tools.

    Usage::

        registry = get_tool_registry()
        tool = registry.get("some_mcp_tool")
        result = await registry.execute("some_mcp_tool", query="hello")
    """

    def __init__(self) -> None:
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        name = tool.name
        self._tools[name] = tool
        logger.debug("Registered tool: %s", name)

    def unregister(self, name: str) -> None:
        """Remove a tool (no-op when absent). Used by MCP reloads."""
        self._tools.pop(name, None)

    def deferred_tools(self) -> list[BaseTool]:
        """Tools flagged for progressive disclosure (see ``BaseTool.deferred``)."""
        return [t for t in self._tools.values() if getattr(t, "deferred", False)]

    def get(self, name: str) -> BaseTool | None:
        return self._tools.get(name)

    def list_tools(self) -> list[str]:
        return list(self._tools)

    def get_enabled(self, names: list[str]) -> list[BaseTool]:
        """Return tool instances for the given names (skipping unknown)."""
        enabled: list[BaseTool] = []
        seen: set[str] = set()
        for name in names:
            tool = self.get(name)
            if tool is None or tool.name in seen:
                continue
            enabled.append(tool)
            seen.add(tool.name)
        return enabled

    def get_definitions(self, names: list[str] | None = None) -> list[ToolDefinition]:
        """Return definitions for *names* (or all if None)."""
        tools = self.get_enabled(self.list_tools()) if names is None else self.get_enabled(names)
        return [t.get_definition() for t in tools]

    def build_openai_schemas(self, names: list[str] | None = None) -> list[dict[str, Any]]:
        """Build OpenAI function-calling tool schemas."""
        return [d.to_openai_schema() for d in self.get_definitions(names)]

    async def execute(self, name: str, /, **kwargs: Any):
        """Execute the tool and return its ToolResult.

        ``name`` (the tool to run) is positional-only so it never collides
        with a tool argument that happens to also be called ``name`` — e.g.
        an MCP tool whose schema declares a ``name`` parameter. All callers
        already pass the tool name positionally.
        """
        tool = self._tools.get(name)
        if tool is None:
            raise KeyError(f"Unknown tool: {name}")
        return await tool.execute(**kwargs)


_default_registry: ToolRegistry | None = None


def get_tool_registry() -> ToolRegistry:
    """Return the global ToolRegistry (creating it on first call)."""
    global _default_registry
    if _default_registry is None:
        _default_registry = ToolRegistry()
    return _default_registry
