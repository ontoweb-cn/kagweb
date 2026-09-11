"""``resolve_agent_model_for_selection`` — the per-turn model name resolver.

The selection has already passed grant validation upstream; this resolver is
pure name lookup over the effective catalog (personal profiles merged), so a
dangling reference degrades to "" (backend default) instead of failing.
"""

from __future__ import annotations

import pytest

from kagweb.services.model_selection import resolve_agent_model_for_selection

_CATALOG = {
    "services": {
        "llm": {
            "profiles": [
                {
                    "id": "prof-1",
                    "name": "Main",
                    "models": [
                        {"id": "m-1", "name": "DeepSeek", "model": "deepseek-v3"},
                        {"id": "m-2", "name": "GPT", "model": "gpt-5"},
                    ],
                }
            ]
        }
    }
}


@pytest.fixture
def catalog(monkeypatch):
    from kagweb.services.config import model_catalog as mc

    service = type("S", (), {"load": staticmethod(lambda: _CATALOG)})()
    monkeypatch.setattr(mc, "get_model_catalog_service", lambda: service)
    return _CATALOG


def test_known_selection_resolves_the_model_string(catalog) -> None:
    assert (
        resolve_agent_model_for_selection({"profile_id": "prof-1", "model_id": "m-1"})
        == "deepseek-v3"
    )


def test_second_model_in_the_same_profile(catalog) -> None:
    assert resolve_agent_model_for_selection({"profile_id": "prof-1", "model_id": "m-2"}) == "gpt-5"


def test_dangling_profile_or_model_degrades_to_empty(catalog) -> None:
    assert resolve_agent_model_for_selection({"profile_id": "nope", "model_id": "m-1"}) == ""
    assert resolve_agent_model_for_selection({"profile_id": "prof-1", "model_id": "nope"}) == ""


def test_missing_or_malformed_selections_degrade_to_empty(catalog) -> None:
    assert resolve_agent_model_for_selection(None) == ""
    assert resolve_agent_model_for_selection({}) == ""
    assert resolve_agent_model_for_selection({"model_id": "m-1"}) == ""
    assert resolve_agent_model_for_selection("not-a-dict") == ""


def test_personal_profiles_are_visible_to_the_resolver(monkeypatch) -> None:
    """Owner-bound (Codex) profiles live outside the shared catalog but are
    part of the effective view the user picked from."""
    from kagweb.services.config import model_catalog as mc

    personal = {
        "services": {
            "llm": {
                "profiles": [
                    {
                        "id": "pers-1",
                        "models": [{"id": "pm-1", "model": "codex-max"}],
                    }
                ]
            }
        }
    }
    service = type(
        "S", (), {"load": staticmethod(lambda: {"services": {"llm": {"profiles": []}}})}
    )()
    monkeypatch.setattr(mc, "get_model_catalog_service", lambda: service)
    import kagweb.multi_user.personal_models as pm

    monkeypatch.setattr(pm, "merge_personal_llm_profiles", lambda _c: personal)

    assert (
        resolve_agent_model_for_selection({"profile_id": "pers-1", "model_id": "pm-1"})
        == "codex-max"
    )
