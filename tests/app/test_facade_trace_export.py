"""DeepMentorApp.export_session_trace — SDK trace export (module 18)."""

from __future__ import annotations

import json

import pytest


@pytest.fixture()
def app(monkeypatch):
    from deepmentor.app.facade import DeepMentorApp

    facade = DeepMentorApp.__new__(DeepMentorApp)  # skip container bootstrapping
    messages = [
        {
            "id": 1,
            "role": "user",
            "content": "what is fourier?",
            "capability": None,
            "events": [],
            "parent_message_id": None,
        },
    ]

    async def _get_session(session_id: str):
        return {"id": session_id, "messages": messages} if session_id == "s1" else None

    monkeypatch.setattr(facade, "get_session", _get_session)
    return facade


@pytest.mark.asyncio
async def test_export_dsl(app) -> None:
    text = await app.export_session_trace("s1")
    doc = json.loads(text)
    assert doc["version"] == 1
    assert doc["trace"][0]["kind"] == "user"


@pytest.mark.asyncio
async def test_export_mermaid(app) -> None:
    text = await app.export_session_trace("s1", "mermaid")
    assert text.lstrip().startswith("flowchart TD")


@pytest.mark.asyncio
async def test_export_missing_session_returns_none(app) -> None:
    assert await app.export_session_trace("nope") is None


@pytest.mark.asyncio
async def test_export_rejects_unknown_format(app) -> None:
    with pytest.raises(ValueError, match="Unsupported trace format"):
        await app.export_session_trace("s1", "yaml")
