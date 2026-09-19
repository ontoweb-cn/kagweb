# -*- coding: utf-8 -*-
"""KAG 管理面访问判定（设计 §6.2/§6.4，T2 多用户）。

读（管理面查询）admin 恒真；非 admin 看 ``kag_projects`` grant（tri-state）
+ **项目 membership**（M4-B T2）：admin 与项目 owner/成员可访问该项目；无成
员记录的项目除 admin 外任何用户不可见（create 时登记 owner）。写操作（项目
创建/变更）admin 恒允许，非 admin 需 membership。
"""

from __future__ import annotations

import hashlib
import re
from typing import Any


def kag_read_allowed(user: Any, *, kag_configured: bool) -> bool:
    """管理面总体读权限（user 为 CurrentUser 或 None=本地 admin 形态）。"""
    if _is_admin(user):
        return True
    try:
        from kagweb.multi_user.grants import load_grant

        grants = load_grant(_uid(user))
    except Exception:
        return True  # 无多用户体系（本地单用户形态）视同 admin
    if grants.get("kag_projects") is False:
        return False
    return bool(kag_configured)


def project_access_allowed(user: Any, project_id: str, *, kag_configured: bool) -> bool:
    """项目级访问（T2）：admin 恒真；否则需 membership 且 grant 通过。"""
    if _is_admin(user):
        return True
    if not kag_read_allowed(user, kag_configured=kag_configured):
        return False
    from kagweb.services.kag.member_store import project_members

    return _uid(user) in project_members(str(project_id))


def filter_projects_by_access(
    user: Any, projects: list[dict[str, Any]], *, kag_configured: bool
) -> list[dict[str, Any]]:
    """项目列表按访问过滤：admin 全量；否则仅 owner/成员项目。"""
    if _is_admin(user):
        return projects
    if not kag_read_allowed(user, kag_configured=kag_configured):
        return []
    uid = _uid(user)
    from kagweb.services.kag.member_store import project_members

    return [p for p in projects if uid in project_members(str(p.get("projectId") or p.get("id") or ""))]


_OPENSPG_USERNO_RE = re.compile(r"^[A-Za-z0-9_]{6,20}$")
_USERNO_PREFIX = "kagweb_"


def derive_user_no(user_id: str) -> str:
    """KAGWeb 用户 → OpenSPG 归因 userNo（M4-B）。

    KAGWeb user_id 形如 ``u_<32hex>``（34 字符），远超 OpenSPG userNo 的 6-20
    字符/字母数字下划线上限（M0-10 实测）。故确定性缩短：``kagweb_``(7) +
    sha256(user_id) 前 12 hex（48bit）＝ 19 字符，唯一且稳定（48bit 的碰撞
    上界远高于现实用户数——评审 M-10：原 8 hex/32bit 约 6.5 万用户即撞 50%）。
    归因语义：给定 user_id 恒得同一 userNo；KAGWeb 侧以同一函数登记，审计时
    能定位到归属用户。
    """
    user_id = str(user_id or "")
    suffix = hashlib.sha256(user_id.encode("utf-8")).hexdigest()[:12]
    user_no = f"{_USERNO_PREFIX}{suffix}"
    assert _OPENSPG_USERNO_RE.match(user_no), f"derived userNo invalid: {user_no}"
    return user_no


def _uid(user: Any) -> str:
    return str(getattr(user, "user_id", "") or str(getattr(user, "id", "") or ""))


def _is_admin(user: Any) -> bool:
    return bool(getattr(user, "is_admin", False)) or getattr(user, "role", "user") == "admin"
