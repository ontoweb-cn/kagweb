"""WebSocket routes must not inherit HTTP-only application dependencies."""

from fastapi.routing import APIWebSocketRoute

from kagweb.api.routers import partner_groups, partners, unified_ws
from kagweb.api.routers.auth import require_learning_surface


def test_websocket_routes_share_one_canonical_namespace() -> None:
    # include_router prefixes are applied at mount time, so the router-local
    # paths carry only the tail of each canonical route.
    expected_paths = {
        "/ws",
        "/ws/partners/{partner_id}",
        "/ws/partner-groups/{group_id}",
    }
    prefixes = {
        id(unified_ws.router): "",
        id(partners.ws_router): "/ws/partners",
        id(partner_groups.ws_router): "/ws/partner-groups",
    }
    websocket_routes: dict[str, APIWebSocketRoute] = {}
    for router in (unified_ws.router, partners.ws_router, partner_groups.ws_router):
        prefix = prefixes[id(router)]
        for route in router.routes:
            if isinstance(route, APIWebSocketRoute):
                websocket_routes[prefix + route.path] = route

    assert set(websocket_routes) == expected_paths
    for route in websocket_routes.values():
        assert all(
            dependency.call is not require_learning_surface
            for dependency in route.dependant.dependencies
        )
