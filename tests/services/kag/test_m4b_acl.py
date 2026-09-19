# -*- coding: utf-8 -*-
"""M4-B T2 多用户 ACL：member_store + access（userNo 派生的契约测试）。

KAGWeb user_id 形如 ``u_<32hex>``（34 字符），OpenSPG userNo 约束 6-20 字符/
字母数字下划线（M0-10 实测）——派生必须落在该窗口内且稳定唯一。
"""

from __future__ import annotations

from types import SimpleNamespace

from kagweb.services.kag import access
from kagweb.services.kag.member_store import (
    ensure_project_owner,
    project_members,
    update_project_members,
)


# —— user_no 派生 ——

def test_derive_user_no_is_stable_and_matches_openspg_constraint() -> None:
    user_id = "u_" + "a" * 32
    a = access.derive_user_no(user_id)
    b = access.derive_user_no(user_id)
    assert a == b  # 同一 user 恒得同一 userNo
    import re

    assert re.match(r"^[A-Za-z0-9_]{6,20}$", a)
    assert a.startswith("kagweb_")
    assert len(a) <= 20


def test_derive_user_no_distinguishes_users() -> None:
    assert (
        access.derive_user_no("u_" + "1" * 32)
        != access.derive_user_no("u_" + "2" * 32)
    )


def test_derive_user_no_collision_resistance_review_m10() -> None:
    """评审 M-10：48bit 后缀对 100k 用户规模零碰撞（原 8 hex/32bit 约 6.5 万
    用户 50% 碰撞）。"""
    suffix = None
    user_nos = set()
    for i in range(100_000):
        uid = f"u_{i:032x}"
        now = access.derive_user_no(uid)
        assert now not in user_nos
        user_nos.add(now)
        if suffix is None:
            suffix = now.split("_", 1)[1]
    assert suffix is not None
    assert len(suffix) == 12  # kagweb_ + 12 hex = 19 字符


# —— member_store ——

def test_ensure_owner_then_members(tmp_path, monkeypatch) -> None:
    from pathlib import Path

    monkeypatch.setattr("kagweb.multi_user.paths.SYSTEM_ROOT", tmp_path)
    assert ensure_project_owner("p1", "owner1") is True
    assert project_members("p1") == ["owner1"]
    assert ensure_project_owner("p1", "other") is False  # 幂等：不覆盖 owner
    # 更新成员：owner 保留在列表首位
    entry = update_project_members("p1", ["bob", "alice"])
    assert entry["owner"] == "owner1"
    assert entry["members"][0] == "owner1"
    assert set(entry["members"][1:]) == {"bob", "alice"}


def test_project_access_allowed_rules(tmp_path, monkeypatch) -> None:
    import sys

    sys.path.insert(0, "/Users/simon/project/kagweb")
    monkeypatch.setattr("kagweb.multi_user.paths.SYSTEM_ROOT", tmp_path)
    ensure_project_owner("p1", "bob")  # bob 为 owner → 天然成员
    admin = SimpleNamespace(role="admin")
    member = SimpleNamespace(user_id="bob", role="user", is_admin=False)
    outsider = SimpleNamespace(user_id="eve", role="user", is_admin=False)

    # admin 恒真
    assert access.project_access_allowed(admin, "p1", kag_configured=True) is True
    # 成员 true（grant 默认 None→跟随部署）
    assert access.project_access_allowed(member, "p1", kag_configured=True) is True
    # 非成员 false
    assert access.project_access_allowed(outsider, "p1", kag_configured=True) is False
    # 未启用 kag → false（grant 门）
    assert access.project_access_allowed(outsider, "p1", kag_configured=False) is False


def test_filter_projects_by_access(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("kagweb.multi_user.paths.SYSTEM_ROOT", tmp_path)
    ensure_project_owner("p1", "alice")
    ensure_project_owner("p2", "bob")
    projects = [
        {"projectId": "p1", "name": "a"},
        {"projectId": "p2", "name": "b"},
        {"projectId": "p3", "name": "c"},
    ]
    alice = SimpleNamespace(user_id="alice", role="user", is_admin=False)
    admin = SimpleNamespace(role="admin")
    # alice 见 p1；p3 无成员记录一律不可见
    assert [p["projectId"] for p in access.filter_projects_by_access(
        alice, projects, kag_configured=True)] == ["p1"]
    # admin 见全部（含无成员项目的 p3）
    assert [p["projectId"] for p in access.filter_projects_by_access(
        admin, projects, kag_configured=True)] == ["p1", "p2", "p3"]


def test_get_tasks_filters_by_membership(tmp_path, monkeypatch) -> None:
    """评审 M-7：非 admin 的 /api/kag/tasks 只返回其可见项目的任务。"""
    import kagweb.multi_user.paths as paths_mod
    import kagweb.services.kag.task_store as store_mod

    monkeypatch.setattr(paths_mod, "SYSTEM_ROOT", tmp_path)
    monkeypatch.setattr(store_mod, "_store_path", lambda: tmp_path / "kag_tasks.json")
    from kagweb.services.kag.task_store import append_task

    ensure_project_owner("p1", "alice")
    ensure_project_owner("p2", "bob")
    append_task({"task_id": "a1", "project_id": "p1", "question": "in my project"})
    append_task({"task_id": "b2", "project_id": "p2", "question": "not mine"})

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from kagweb.api.routers.kag import router

    app = FastAPI()
    app.include_router(router, prefix="/api/kag")
    client = TestClient(app)

    # alice（非 admin）：p1 成员 → 只见 a1
    def fake_current():
        return SimpleNamespace(user_id="alice", role="user", is_admin=False)

    monkeypatch.setattr("kagweb.api.routers.kag._current_user", fake_current)
    monkeypatch.setattr("kagweb.api.routers.kag.kag_enabled", lambda: True)
    from kagweb.services.kag import openspg_client

    def fake_client():
        return openspg_client.OpenSPGClient("http://spg.test", transport=_mock_transport())

    monkeypatch.setattr("kagweb.api.routers.kag._client", fake_client)
    resp = client.get("/api/kag/tasks")
    assert resp.status_code == 200
    task_ids = [t["task_id"] for t in resp.json()["tasks"]]
    assert task_ids == ["a1"]


def test_members_write_requires_admin_even_if_member(tmp_path, monkeypatch) -> None:
    """评审（A）：前端 can_edit 只控 UX，后端 PUT /members 必须 admin——
    owner（成员）也不能改，防横向越权改其他成员关系。"""
    monkeypatch.setattr("kagweb.multi_user.paths.SYSTEM_ROOT", tmp_path)
    ensure_project_owner("p1", "bob")

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from kagweb.api.routers.kag import router

    app = FastAPI()
    app.include_router(router, prefix="/api/kag")
    client = TestClient(app)

    def non_admin():
        return SimpleNamespace(user_id="bob", role="user", is_admin=False)

    monkeypatch.setattr("kagweb.api.routers.kag._current_user", non_admin)
    monkeypatch.setattr("kagweb.api.routers.kag.kag_enabled", lambda: True)
    from kagweb.services.kag import openspg_client

    def fake_client():
        return openspg_client.OpenSPGClient("http://spg.test", transport=_mock_transport())

    monkeypatch.setattr("kagweb.api.routers.kag._client", fake_client)
    # bob 是 owner（成员），但仍不可改成员
    resp = client.put("/api/kag/projects/p1/members", json={"members": ["alice"]})
    assert resp.status_code == 403
    # 成员 GET 可读（owner/members 门禁通过），且 can_edit=false
    resp = client.get("/api/kag/projects/p1/members")
    assert resp.status_code == 200
    assert resp.json()["can_edit"] is False


def _mock_transport():
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        # list_projects 返回 p1/p2
        return httpx.Response(200, json=[
            {"projectId": "p1", "name": "a"},
            {"projectId": "p2", "name": "b"},
        ])

    return httpx.MockTransport(handler)