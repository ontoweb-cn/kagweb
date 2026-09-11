"""The grants editor's assignable MCP catalog (``/admin/resources``).

The listed names come from the same process registry the runtime allowlist
filters (``view.py``), so anything shown here is exactly whitelistable.
"""

from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Open-admin client: auth disabled promotes every request to admin
    (the same seam the real single-user deployment uses)."""
    import kagweb.api.routers.auth as auth_router

    monkeypatch.setattr(auth_router, "AUTH_ENABLED", False)
    from fastapi import FastAPI

    from kagweb.api.routers import multi_user

    app = FastAPI()
    app.include_router(multi_user.router, prefix="/api/multi-user")
    return TestClient(app)


class _StubTool:
    def __init__(self, name: str, provider: str, description: str):
        self._name = name
        self._provider = provider
        self._description = description
        self.deferred = True
        self.provider_id = provider

    def get_definition(self):
        from kagweb.core.tool_protocol import ToolDefinition

        return ToolDefinition(name=self._name, description=self._description)


@pytest.fixture
def stub_registry(monkeypatch):
    """Patch the process registry with two MCP tools (deferred) and one other."""
    from kagweb.runtime.registry import tool_registry as tr

    tools = [
        _StubTool("mcp_fs_read", "fs", "[fs] Read a file"),
        _StubTool("mcp_web_search", "web", "[web] Search the web"),
        _StubTool("not_deferred", "x", "should never be listed"),
    ]
    tools[2].deferred = False

    class _Registry:
        def deferred_tools(self):
            return [t for t in tools if t.deferred]

    monkeypatch.setattr(tr, "get_tool_registry", lambda: _Registry())
    return tools


@pytest.fixture
def stub_manager(monkeypatch):
    """Patch the MCP manager so ensure_started resolves without connecting."""
    from kagweb.services import mcp as mcp_pkg

    class _Manager:
        async def ensure_started(self) -> None:
            return None

    manager = _Manager()
    monkeypatch.setattr(mcp_pkg, "get_mcp_manager", lambda: manager)
    return manager


def _resources(client):
    response = client.get("/api/multi-user/admin/resources")
    assert response.status_code == 200
    return response.json()


def test_resources_list_the_mcp_catalog(client, stub_registry, stub_manager):
    data = _resources(client)

    names = [tool["name"] for tool in data["mcp_tools"]]
    # Sorted, deferred-only, with the grouping key the editor renders by.
    assert names == ["mcp_fs_read", "mcp_web_search"]
    assert data["mcp_tools"][0]["provider_id"] == "fs"
    assert data["mcp_tools"][0]["kind"] == "mcp"
    assert "Read a file" in data["mcp_tools"][0]["description"]


def test_resources_names_match_the_runtime_allowlist_pool(client, stub_registry, stub_manager):
    """Consistency contract: every name the editor shows is a name the
    scoped-registry allowlist will accept (same source: deferred_tools)."""
    from kagweb.runtime.registry.tool_registry import get_tool_registry

    data = _resources(client)
    listed = {tool["name"] for tool in data["mcp_tools"]}
    allowlist_pool = {tool.get_definition().name for tool in get_tool_registry().deferred_tools()}
    assert listed == allowlist_pool


def test_resources_degrade_to_empty_when_mcp_fails(client, monkeypatch):
    """A broken MCP manager must not fail the admin page — the catalog is
    empty and the existing grant whitelist stays editable."""
    from kagweb.services import mcp as mcp_pkg

    class _Broken:
        async def ensure_started(self) -> None:
            raise RuntimeError("MCP down")

    monkeypatch.setattr(mcp_pkg, "get_mcp_manager", lambda: _Broken())

    data = _resources(client)
    assert data["mcp_tools"] == []
    assert "models" in data


def test_resources_degrade_to_empty_on_slow_mcp(client, monkeypatch):
    import asyncio

    from kagweb.services import mcp as mcp_pkg

    class _Slow:
        async def ensure_started(self) -> None:
            await asyncio.sleep(30)

    monkeypatch.setattr(mcp_pkg, "get_mcp_manager", lambda: _Slow())
    import kagweb.api.routers.multi_user as mu

    monkeypatch.setattr(mu, "_MCP_CATALOG_START_TIMEOUT", 0.1)

    data = _resources(client)
    assert data["mcp_tools"] == []
