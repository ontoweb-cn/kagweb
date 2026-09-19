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