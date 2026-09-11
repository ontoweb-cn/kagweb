"""Legacy device-credential tokens must not survive the subsystem removal."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from kagweb.services import auth as auth_service


def _mint(claims: dict) -> str:
    from jose import jwt

    payload = {
        "sub": "alice",
        "role": "user",
        "uid": "u_1",
        "exp": datetime.now(timezone.utc) + timedelta(hours=1),
        "iat": datetime.now(timezone.utc),
        **claims,
    }
    return jwt.encode(payload, auth_service.AUTH_SECRET, algorithm=auth_service._ALGORITHM)


def test_device_claims_reject_the_token(monkeypatch, tmp_path):
    """A token carrying the removed dcid/dcs claims is dead on arrival."""
    monkeypatch.setattr(auth_service, "AUTH_SECRET", "test-secret")
    monkeypatch.setattr(auth_service, "_ALGORITHM", "HS256")

    assert auth_service.decode_token(_mint({"dcid": "dc_1", "dcs": "nonce"})) is None
    assert auth_service.decode_token(_mint({"dcid": "dc_1"})) is None
    assert auth_service.decode_token(_mint({"dcs": "nonce"})) is None


def test_plain_token_without_device_claims_still_decodes(monkeypatch):
    monkeypatch.setattr(auth_service, "AUTH_SECRET", "test-secret")
    monkeypatch.setattr(auth_service, "_ALGORITHM", "HS256")

    payload = auth_service.decode_token(_mint({}))
    assert payload is not None
    assert (payload.username, payload.role, payload.user_id) == ("alice", "user", "u_1")
