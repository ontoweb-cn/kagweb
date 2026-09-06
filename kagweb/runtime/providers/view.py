"""Assemble one turn's external-provider tool surface.

This is the composition seam that used to live inline in the chat pipeline:
start the providers, work out what this caller may use, filter the pool,
build the progressive-disclosure loader, and render the manifest block. It
lives here so the policy is testable on its own and so a second consumer (or
a second provider kind) does not mean a second copy inside a pipeline.

Contract: :func:`build_tool_view` **never raises**. A provider that is down,
misconfigured, or slow degrades to "no external tools this turn" — the turn
itself must still run.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
from typing import Any, Iterable

from kagweb.core.tool_protocol import BaseTool, ToolLookup
from kagweb.runtime.providers.allowlist import Allowlist
from kagweb.runtime.providers.authorize import authorize_mcp_tools
from kagweb.runtime.providers.scope import ToolScope
from kagweb.runtime.registry.deferred_tools import (
    DeferredToolLoader,
    render_deferred_tools_manifest,
)
from kagweb.runtime.registry.scoped_registry import ScopedToolRegistry

logger = logging.getLogger(__name__)

#: Ceiling on connecting a caller's own servers. This runs before the turn's
#: first stream event, so it is the one place a slow third-party host could
#: present as "KAGWeb hung".
_OWNER_SCOPE_TIMEOUT_S = 3.0


@dataclass(frozen=True)
class ProviderToolView:
    """One turn's resolved provider surface."""

    #: Registry the turn should use for lookup and dispatch (scoped view).
    registry: ToolLookup
    #: ``None`` when this turn has no external tools at all.
    loader: DeferredToolLoader | None
    #: Provider tools visible this turn — the manifest's contents, and what
    #: the context-budget chip counts as "unloaded extended tools".
    pool: tuple[BaseTool, ...]
    #: Rendered ``extended_tools`` system-prompt block ("" when suppressed).
    manifest: str

    @classmethod
    def empty(cls, registry: ToolLookup) -> "ProviderToolView":
        return cls(registry=registry, loader=None, pool=(), manifest="")

    def attach(self, tool_schemas: list[dict[str, Any]]) -> None:
        """Add already-loaded schemas and bind the turn's live schema list.

        The loader appends to this list as the model calls ``load_tools``, and
        the agent loop re-reads it every round, so tools become callable
        without rebuilding the request.
        """
        if self.loader is None:
            return
        tool_schemas.extend(self.loader.initial_schemas())
        self.loader.bind_live_schemas(tool_schemas)


async def build_tool_view(
    *,
    base_registry: ToolLookup,
    scope: ToolScope,
    language: str = "en",
    refusal_message: str = "",
    overlay_tools: Iterable[BaseTool] = (),
    preloaded_names: Iterable[str] = (),
) -> ProviderToolView:
    """Resolve the provider tools *scope* may use this turn."""
    try:
        return await _build(
            base_registry=base_registry,
            scope=scope,
            language=language,
            refusal_message=refusal_message,
            overlay_tools=tuple(overlay_tools),
            preloaded_names=frozenset(preloaded_names),
        )
    except Exception:
        logger.warning("provider tool view assembly failed; continuing without", exc_info=True)
        return ProviderToolView.empty(base_registry)


async def _build(
    *,
    base_registry: ToolLookup,
    scope: ToolScope,
    language: str,
    refusal_message: str,
    overlay_tools: tuple[BaseTool, ...],
    preloaded_names: frozenset[str],
) -> ProviderToolView:
    from kagweb.services.mcp import get_mcp_manager, load_loaded_tools

    manager = get_mcp_manager()
    await manager.ensure_started()

    shared_pool = list(base_registry.deferred_tools())
    owned_pool = await _owned_tools(manager, scope)
    if not shared_pool and not owned_pool and not overlay_tools:
        return ProviderToolView.empty(base_registry)

    allowed = authorize_mcp_tools(
        scope=scope,
        user_grant=_user_grant(scope),
        # Servers the caller configured themselves are authorised by ownership:
        # the admin grant governs the deployment's shared servers, and applying
        # it here would make self-service configuration silently useless.
        owned_names=[tool.get_definition().name for tool in owned_pool],
    )
    # Resource-bound overlays (including PageIndex SDK tools) are authorised by
    # possession of the selected resource and live only in this turn's registry.
    allowed = allowed.widen(tool.get_definition().name for tool in overlay_tools)

    pool = tuple(
        tool
        for tool in (*shared_pool, *owned_pool, *overlay_tools)
        if allowed.allows(tool.get_definition().name)
    )
    registry = ScopedToolRegistry(
        base=base_registry,
        # Owner-scoped tools live only in this turn's overlay — they are never
        # published to the process registry, so two accounts whose servers share
        # a name cannot resolve to each other's session.
        overlay=[*owned_pool, *overlay_tools],
        allowed=allowed,
        refusal_message=refusal_message,
    )
    if not pool:
        # Nothing authorised: no manifest, no loader — but keep the scoped
        # registry so dispatch still refuses names the model may invent.
        return ProviderToolView(registry=registry, loader=None, pool=(), manifest="")

    loader = DeferredToolLoader(
        registry=registry,
        session_id=scope.session_id,
        # Resource-authorised tools are preloaded: holding the resource is the
        # permission, so the model should not have to spend a `load_tools`
        # round-trip before its first retrieval.
        loaded=load_loaded_tools(scope.session_id) | set(preloaded_names),
        allowed=allowed.as_set(),
    )
    manifest = (
        ""
        if scope.exclusive_capability
        else render_deferred_tools_manifest(list(pool), language=language)
    )
    return ProviderToolView(registry=registry, loader=loader, pool=pool, manifest=manifest)


async def _owned_tools(manager: Any, scope: ToolScope) -> list[BaseTool]:
    """Tools from servers this caller configured for themselves.

    Bounded by a per-scope timeout: connecting somebody's own remote server is a
    network round-trip on the turn's critical path, before a single stream event
    has been emitted. A slow server must cost this turn its own tools, not the
    whole turn's first token.
    """
    if scope.is_partner or not scope.owner_id:
        # A partner has no account of its own, so it has no self-configured
        # servers; its surface is the deployment's, filtered by its own list.
        return []
    try:
        return list(
            await asyncio.wait_for(
                manager.ensure_scope(scope.owner_id),
                timeout=_OWNER_SCOPE_TIMEOUT_S,
            )
        )
    except asyncio.TimeoutError:
        logger.warning(
            "per-user MCP scope for %s did not connect in %ss; continuing without",
            scope.owner_id,
            _OWNER_SCOPE_TIMEOUT_S,
        )
        return []
    except Exception:
        logger.warning("per-user MCP scope for %s failed", scope.owner_id, exc_info=True)
        return []


def _user_grant(scope: ToolScope) -> Allowlist:
    """The caller's ``grant.mcp_tools``, or unrestricted for a partner turn.

    A partner has no account and therefore no grant; its surface is decided by
    the partner's own configured whitelist, which reaches us as
    ``scope.caller_whitelist``.
    """
    if scope.is_partner:
        return Allowlist.unrestricted()
    from kagweb.multi_user.tool_access import allowed_mcp_tools

    return Allowlist.of(allowed_mcp_tools())


__all__ = ["ProviderToolView", "build_tool_view"]
