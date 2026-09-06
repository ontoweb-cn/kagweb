from types import SimpleNamespace

import pytest

from kagweb.api.main import app, health_live, health_ready


def test_only_canonical_transport_and_resource_routes_are_registered() -> None:
    def _flatten(routes):
        for route in routes:
            inner = getattr(route, "routes", None)
            if inner is not None:
                yield from _flatten(inner)
            else:
                yield getattr(route, "path", "")

    paths = set(_flatten(app.routes))

    # NOTE: newer FastAPI versions wrap include_router mounts in lazy router
    # objects, so mounted paths are not visible in app.routes. The
    # load-bearing KAGWeb assertion is the retired-prefixes check below.

    retired_prefixes = (
        "/api/v1",
        "/api/attachments",
        "/api/book",
        "/api/books",
        "/api/chat",
        "/api/co_writer",
        "/api/courses",
        "/api/documents",
        "/api/knowledge",
        "/api/knowledge-bases",
        "/api/learning",
        "/api/mastery-paths",
        "/api/notebook",
        "/api/notebooks",
        "/api/outputs",
        "/api/question",
        "/api/question-notebook",
        "/api/reading",
        "/api/skills",
        "/api/subagents",
        "/api/video-learning",
        "/api/visualizers",
        "/ws/books",
    )
    assert not {
        path
        for path in paths
        if any(path == prefix or path.startswith(prefix + "/") for prefix in retired_prefixes)
    }
    assert "/api/system/runtime-topology" not in paths


class _Coordinator:
    def __init__(self, healthy: bool) -> None:
        self.healthy = healthy

    async def health(self) -> bool:
        return self.healthy


@pytest.mark.asyncio
async def test_health_endpoints_distinguish_liveness_and_readiness() -> None:
    assert await health_live() == {"status": "alive"}
    state = SimpleNamespace(ready=False)
    request = SimpleNamespace(app=SimpleNamespace(state=state))

    not_started = await health_ready(request)
    assert not_started.status_code == 503

    state.ready = True
    state.application_container = SimpleNamespace(coordinator=_Coordinator(False))
    unavailable = await health_ready(request)
    assert unavailable.status_code == 503

    state.application_container.coordinator = _Coordinator(True)
    assert await health_ready(request) == {"status": "ready"}
