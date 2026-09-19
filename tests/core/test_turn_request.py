"""TurnRequest contract guards: the fields a hostile client can push values
into must carry their own bounds (the composer model-selector design, T1)."""

from __future__ import annotations

from pydantic import ValidationError
import pytest

from kagweb.core.turn_request import TurnRequest


def _payload(**extra: object) -> dict:
    return {"content": "hi", **extra}


def test_backend_model_accepts_a_normal_id() -> None:
    request = TurnRequest.model_validate(_payload(backend_model="deepseek:deepseek-flash"))
    assert request.backend_model == "deepseek:deepseek-flash"


def test_backend_model_rejects_overlong_values() -> None:
    """The cap bounds what a hostile client can push into a CLI argv element
    or an HTTP body field; model ids are nowhere near it."""
    with pytest.raises(ValidationError):
        TurnRequest.model_validate(_payload(backend_model="x" * 257))
    assert TurnRequest.model_validate(_payload(backend_model="x" * 256)).backend_model == (
        "x" * 256
    )


def test_backend_model_none_and_omitted_stay_equivalent() -> None:
    assert TurnRequest.model_validate(_payload()).backend_model is None
    payload = TurnRequest.model_validate(_payload(backend_model=None))
    assert payload.backend_model is None
