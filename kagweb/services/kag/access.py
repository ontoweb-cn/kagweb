# -*- coding: utf-8 -*-
"""KAG 管理面访问判定（设计 §6.2/§6.4，T1 子集）。

T1 单租户：读（管理面查询）admin 恒真；非 admin 看 ``kag_projects`` grant
（tri-state：``None`` 跟随部署——kag 已配置即可读，语义沿 ``agent_loop``
先例；``False`` 显式禁用）。写操作（项目创建/变更）在 T2 ACL 落地前仅 admin。
"""

from __future__ import annotations

from typing import Any


def kag_read_allowed(user: Any, *, kag_configured: bool) -> bool:
    """管理面读权限（user 为 CurrentUser 或 None=本地 admin 形态）。"""
    if bool(getattr(user, "is_admin", False)) or getattr(user, "role", "user") == "admin":
        return True
    try:
        from kagweb.multi_user.grants import load_grant

        grants = load_grant(str(getattr(user, "user_id", "") or ""))
    except Exception:
        return True  # 无多用户体系（本地单用户形态）视同 admin
    if grants.get("kag_projects") is False:
        return False
    return bool(kag_configured)
