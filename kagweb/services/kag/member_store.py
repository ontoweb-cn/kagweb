# -*- coding: utf-8 -*-
"""KAG 项目成员存储（M4-B，设计 §6.4 阶段 T2 多用户 ACL）。

``data/system/kag_project_members.json``：``{project_id: {"owner": <uid>,
"members": [<uid>…]}}``。owner 恒在 members 中（读语义等价）；admin 恒可访问
全部项目（不进本表）；未在表中的项目视为 **无成员**——除 admin 外任何用户都
不可见（新项目经 create 时登记 owner）。

沿 task_store 的 JSON 文件模式（设计偏离已记录）：记录体量小、滚动域名可控、
零 schema 迁移；F5 线程锁不跨进程的已知限制沿续（成员变更低频、advisory 语
义可接受，M4 不迁移 SQLite）。
"""

from __future__ import annotations

import json
import threading
from typing import Any

_lock = threading.Lock()


def _store_path() -> Any:
    from kagweb.multi_user.paths import SYSTEM_ROOT

    SYSTEM_ROOT.mkdir(parents=True, exist_ok=True)
    return SYSTEM_ROOT / "kag_project_members.json"


def _load() -> dict[str, Any]:
    try:
        data = json.loads(_store_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(data: dict[str, Any]) -> None:
    _store_path().write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def ensure_project_owner(project_id: str, owner_uid: str) -> bool:
    """登记项目 owner（create 时调用；幂等——已存在则保留原 owner）。

    若项目此前无成员记录，以 owner_uid 为 owner 并加入 members；否则不改动。
    """
    project_id = str(project_id)
    with _lock:
        data = _load()
        entry = data.get(project_id)
        if isinstance(entry, dict) and entry.get("owner"):
            return False
        data[project_id] = {"owner": str(owner_uid), "members": [str(owner_uid)]}
        _save(data)
        return True


def project_members(project_id: str) -> list[str]:
    """项目成员 uid 列表（无记录返回空；owner 恒在列）。"""
    entry = _load().get(str(project_id))
    if not isinstance(entry, dict):
        return []
    owner = str(entry.get("owner") or "")
    members = [str(u) for u in (entry.get("members") or []) if str(u)]
    if owner and owner not in members:
        members.insert(0, owner)
    return members


def project_owner(project_id: str) -> str:
    return str((_load().get(str(project_id)) or {}).get("owner") or "")


def update_project_members(project_id: str, members: list[str]) -> dict[str, Any]:
    """设置成员（owner 不变；参数不含 owner 时保留）。admin 维护入口。"""
    project_id = str(project_id)
    normalized = [str(u) for u in members if str(u)]
    with _lock:
        data = _load()
        entry = data.get(project_id)
        if not isinstance(entry, dict) or not entry.get("owner"):
            raise KeyError(f"project has no owner yet: {project_id}")
        owner = str(entry["owner"])
        if owner in normalized:
            normalized.remove(owner)
        # owner 恒在列表首位；其余去重保序
        seen = {owner}
        rest = [u for u in normalized if not (u in seen or seen.add(u))]
        entry["members"] = [owner, *rest]
        entry["owner"] = owner
        data[project_id] = entry
        _save(data)
        return entry