# -*- coding: utf-8 -*-
"""M2 后端单测：OpenSPG 客户端（MockTransport）、任务存储、访问判定、
grants 新维度、settings 域脱敏。设计 docs/kag-integration-design.md §5.2/§6。"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from kagweb.services.kag.openspg_client import OpenSPGClient, OpenSPGError


# ---------------------------------------------------------------------------
# OpenSPGClient（httpx.MockTransport，无网络）
# ---------------------------------------------------------------------------


def _mock_client(handler) -> OpenSPGClient:
    return OpenSPGClient("http://spg.test", transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_list_projects_parses_json() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json=[{"id": 1, "namespace": "m0ProbeLive"}])

    client = _mock_client(handler)
    projects = await client.list_projects()
    assert projects == [{"id": 1, "namespace": "m0ProbeLive"}]
    assert seen == ["http://spg.test/public/v1/project"]


@pytest.mark.asyncio
async def test_error_maps_string_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        # M0-4 实测：server 的校验错误是 4xx + 裸字符串
        return httpx.Response(400, text="account(userNo) length is 6-20")

    client = _mock_client(handler)
    with pytest.raises(OpenSPGError) as exc_info:
        await client.list_projects()
    assert exc_info.value.status == 400
    assert "6-20" in exc_info.value.body


@pytest.mark.asyncio
async def test_create_project_local_contract() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"id": 9})

    client = _mock_client(handler)
    await client.create_project(
        name="n", namespace="nsTest", user_no="kagweb", vectorizer={"type": "openai"}
    )
    body = captured["body"]
    # M0-10 实测契约：tag=LOCAL + config.vectorizer 必需
    assert body["tag"] == "LOCAL"
    assert body["config"]["vectorizer"] == {"type": "openai"}
    assert body["userNo"] == "kagweb"


@pytest.mark.asyncio
async def test_missing_base_url_raises() -> None:
    client = OpenSPGClient("")
    with pytest.raises(OpenSPGError):
        await client.list_projects()


@pytest.mark.asyncio
async def test_query_schema_uses_verified_path() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json={"spgTypes": []})

    await _mock_client(handler).query_schema(3)
    # M0-4 实测命中的候选 A 路径
    assert seen[0].startswith("http://spg.test/public/v1/schema/queryProjectSchema")
    assert "projectId=3" in seen[0]


# ---------------------------------------------------------------------------
# 任务存储（task_store；SYSTEM_ROOT 重定向到 tmp）
# ---------------------------------------------------------------------------


@pytest.fixture()
def _task_store_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    import kagweb.multi_user.paths as paths_mod

    monkeypatch.setattr(paths_mod, "SYSTEM_ROOT", tmp_path)
    import kagweb.services.kag.task_store as store_mod

    monkeypatch.setattr(store_mod, "_store_path", lambda: tmp_path / "kag_tasks.json")
    return tmp_path


def test_task_store_append_list_filter(_task_store_root: Path) -> None:
    from kagweb.services.kag.task_store import append_task, list_tasks

    append_task({"task_id": "t1", "session_id": "s1", "project_id": "3", "question": "q1"})
    append_task({"task_id": "t2", "session_id": "s2", "project_id": "9", "question": "q2"})
    assert [t["task_id"] for t in list_tasks()] == ["t2", "t1"]  # 新在前
    assert [t["task_id"] for t in list_tasks(session_id="s1")] == ["t1"]
    assert [t["task_id"] for t in list_tasks(project_id="9")] == ["t2"]


def test_task_store_idempotent_task_id(_task_store_root: Path) -> None:
    from kagweb.services.kag.task_store import append_task, list_tasks

    append_task({"task_id": "t1", "question": "first"})
    append_task({"task_id": "t1", "question": "second"})
    rows = list_tasks()
    assert len(rows) == 1 and rows[0]["question"] == "second"


def test_task_store_rolls_at_limit(_task_store_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from kagweb.services.kag import task_store

    monkeypatch.setattr(task_store, "KAG_TASKS_LIMIT", 3)
    for i in range(5):
        task_store.append_task({"task_id": f"t{i}"})
    rows = task_store.list_tasks()
    assert [r["task_id"] for r in rows] == ["t4", "t3", "t2"]


def test_task_store_corrupt_file_returns_empty(_task_store_root: Path) -> None:
    from kagweb.services.kag.task_store import list_tasks

    (_task_store_root / "kag_tasks.json").write_text("not json", encoding="utf-8")
    assert list_tasks() == []


# ---------------------------------------------------------------------------
# 访问判定与 grants 维度
# ---------------------------------------------------------------------------


class _User:
    def __init__(self, *, user_id: str = "u1", is_admin: bool = False):
        self.user_id = user_id
        self.is_admin = is_admin


def test_access_admin_always_allowed() -> None:
    from kagweb.services.kag.access import kag_read_allowed

    assert kag_read_allowed(_User(is_admin=True), kag_configured=False) is True


def test_access_grant_false_denies(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import kagweb.multi_user.grants as grants_mod
    from kagweb.services.kag.access import kag_read_allowed

    monkeypatch.setattr(
        grants_mod, "load_grant", lambda uid: {"kag_projects": False}
    )
    assert kag_read_allowed(_User(), kag_configured=True) is False


def test_access_grant_none_follows_deployment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import kagweb.multi_user.grants as grants_mod
    from kagweb.services.kag.access import kag_read_allowed

    monkeypatch.setattr(grants_mod, "load_grant", lambda uid: {"kag_projects": None})
    assert kag_read_allowed(_User(), kag_configured=True) is True
    assert kag_read_allowed(_User(), kag_configured=False) is False


def test_grants_normalize_kag_projects() -> None:
    from kagweb.multi_user.grants import empty_grant, normalize_grant

    assert empty_grant("u1")["kag_projects"] is None
    assert normalize_grant("u1", {"kag_projects": False})["kag_projects"] is False
    assert normalize_grant("u1", {"kag_projects": True})["kag_projects"] is True
    assert normalize_grant("u1", {"kag_projects": "yes"})["kag_projects"] is None  # 非法值归 None


# ---------------------------------------------------------------------------
# settings 域脱敏（_kag_domain_payload）
# ---------------------------------------------------------------------------


def test_kag_domain_payload_redacts_key() -> None:
    from kagweb.api.routers.settings import _kag_domain_payload

    payload = _kag_domain_payload(
        {"version": 1, "bridge_command": "/x", "bridge_api_key": "secret"}
    )
    assert payload["bridge_api_key"] == ""  # write-only：不回显
    assert payload["bridge_api_key_set"] is True
    assert payload["bridge_command"] == "/x"


# ---------------------------------------------------------------------------
# 管理面响应脱敏（M2.4 冒烟发现：project.config 携带 vectorizer api_key
# 与图存储密码，list/get/create 直接透传会向浏览器泄露服务端凭据）
# ---------------------------------------------------------------------------


def test_sanitize_masks_project_config_credentials() -> None:
    from kagweb.api.routers.kag import _sanitize

    row = {
        "id": 3,
        "name": "m0ProbeLive",
        "config": {
            "vectorizer": {
                "model": "Qwen3-Embedding-8B",
                "api_key": "gpustack_sk_live",
                "base_url": "https://ai.example/v1",
            },
            "graph_store": {
                "uri": "neo4j://h:7687",
                "user": "neo4j",
                "password": "neo4j@openspg",
            },
        },
    }
    sanitized = _sanitize(row)
    vectorizer = sanitized["config"]["vectorizer"]
    assert vectorizer["api_key"] == "***"
    assert vectorizer["model"] == "Qwen3-Embedding-8B"  # 非敏感字段原样保留
    assert sanitized["config"]["graph_store"]["password"] == "***"
    assert sanitized["config"]["graph_store"]["uri"] == "neo4j://h:7687"
    # 原对象不被就地修改（掩码只作用于响应组装）
    assert row["config"]["vectorizer"]["api_key"] == "gpustack_sk_live"


def test_sanitize_traverses_nested_lists_and_keeps_scalars() -> None:
    from kagweb.api.routers.kag import _sanitize

    value = {"items": [{"token": "t1", "ok": 1}, {"secret": "s"}], "n": None, "s": "x"}
    sanitized = _sanitize(value)
    assert sanitized == {"items": [{"token": "***", "ok": 1}, {"secret": "***"}], "n": None, "s": "x"}


def test_sanitize_masks_serialized_json_config_string() -> None:
    # OpenSPG 真实形态（M0-3/M2.4 冒烟实测）：project.config 是序列化 JSON
    # 字符串，字符串内的凭据同样要掩掉，且保持字符串形态。
    from kagweb.api.routers.kag import _sanitize

    config = json.dumps(
        {
            "vectorizer": {"api_key": "gpustack_sk_live", "model": "Qwen3-Embedding-8B"},
            "graph_store": {"password": "neo4j@openspg", "uri": "neo4j://h:7687"},
        }
    )
    row = {"id": 3, "config": config, "name": "m0ProbeLive"}
    sanitized = _sanitize(row)
    assert isinstance(sanitized["config"], str)
    parsed = json.loads(sanitized["config"])
    assert parsed["vectorizer"]["api_key"] == "***"
    assert parsed["vectorizer"]["model"] == "Qwen3-Embedding-8B"
    assert parsed["graph_store"]["password"] == "***"
    # 无凭据的 JSON 字符串原样保留（不重排键序以外的字节）
    plain = json.dumps({"a": 1, "b": [2, 3]})
    assert _sanitize({"config": plain})["config"] == plain
    # 非 JSON 字符串不受影响
    assert _sanitize({"s": "hello {not json"})["s"] == "hello {not json"
