"""WebSocket routes must not inherit HTTP-only application dependencies."""

from fastapi.routing import APIWebSocketRoute

from kagweb.api.routers import unified_ws
from kagweb.api.routers.auth import require_learning_surface


def test_websocket_routes_share_one_canonical_namespace() -> None:
    # The unified WS router is mounted without a prefix, and it is the only
    # WebSocket surface: every canonical route lives under /ws.
    expected_paths = {"/ws"}
    websocket_routes: dict[str, APIWebSocketRoute] = {}
    for route in unified_ws.router.routes:
        if isinstance(route, APIWebSocketRoute):
            websocket_routes[route.path] = route

    assert set(websocket_routes) == expected_paths
    for route in websocket_routes.values():
        assert all(
            dependency.call is not require_learning_surface
            for dependency in route.dependant.dependencies
        )
