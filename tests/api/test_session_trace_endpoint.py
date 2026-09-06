"""GET /api/sessions/{id}/trace — DSL / Mermaid export endpoint (module 18)."""

from __future__ import annotations

import pytest

FastAPI = pytest.importorskip("fastapi").FastAPI
TestClient = pytest.importorskip("fastapi.testclient").TestClient


def _msg(
    mid: int,
    role: str,
    parent: int | None,
    *,
    content: str = "",
    events: list | None = None,
) -> dict:
    return {
        "id": mid,
        "role": role,
        "content": content,
        "capability": None,
        "events": events or [],
        "parent_message_id": parent,
    }


@pytest.fixture()
def client(monkeypatch) -> TestClient:
    from deepmentor.api.routers import sessions as sessions_router

    class _Store:
        async def get_session_with_messages(self, session_id: str):
            if session_id != "s1":
                return None
            return {
                "id": "s1",
                "messages": [
                    _msg(1, "user", None, content="what is fourier?"),
                    _msg(
                        2,
                        "assistant",
                        1,
                        events=[
                            {
                                "type": "thinking",
                                "source": "chat",
                                "stage": "exploring",
                                "content": "plan",
                                "metadata": {
                                    "call_id": "r1",
                                    "call_kind": "agent_loop_round",
                                    "trace_group": "stage",
                                },
                                "timestamp": 1,
                            },
                        ],
                    ),
                ],
            }

    monkeypatch.setattr(sessions_router, "get_session_store", lambda: _Store())
    app = FastAPI()
    # Prefix is required: the router's list endpoint uses the empty path "".
    app.include_router(sessions_router.router, prefix="/api/sessions")
    return TestClient(app)


def test_trace_dsl_returns_document(client: TestClient) -> None:
    res = client.get("/api/sessions/s1/trace")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("application/json")
    doc = res.json()
    assert doc["version"] == 1
    assert [entry["kind"] for entry in doc["trace"]] == ["user", "assistant"]
    assert doc["trace"][1]["calls"][0]["kind"] == "round"


def test_trace_mermaid_is_plain_text(client: TestClient) -> None:
    res = client.get("/api/sessions/s1/trace", params={"format": "mermaid"})
    assert res.status_code == 200
    # text/plain — the diagram source must never render as HTML (#78)
    assert res.headers["content-type"].startswith("text/plain")
    assert res.text.lstrip().startswith("flowchart TD")


def test_trace_options_pass_through(client: TestClient) -> None:
    plain = client.get("/api/sessions/s1/trace").json()
    stable = client.get(
        "/api/sessions/s1/trace",
        params={"stable": "true", "normalize_ids": "true", "include_text": "false"},
    ).json()
    assert "session" in plain  # volatile block present by default
    assert "session" not in stable
    assert stable["trace"][0].get("text_preview") is None


def test_trace_unknown_session_404(client: TestClient) -> None:
    assert client.get("/api/sessions/missing/trace").status_code == 404


def test_trace_rejects_bad_format_422(client: TestClient) -> None:
    assert client.get("/api/sessions/s1/trace", params={"format": "yaml"}).status_code == 422
