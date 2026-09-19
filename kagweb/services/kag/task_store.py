# -*- coding: utf-8 -*-
"""KAG 推理任务摘要存储（M2，附录 A.3 契约的 KAGWeb 侧落地）。

设计偏离记录（评审决策）：设计 §5.2 写"存 KAGWeb 既有 SQLite/PocketBase"，
M2 v1 改用 JSON 文件（data/system/kag_tasks.json，系统级 bridge 上报记录），
理由：记录体量小（滚动上限）、零 schema 迁移、与 auth_users.json 同模式；
M4 多租户（T2）时按需迁移 SQLite。

已知限制（评审 F5）：``_lock`` 是线程锁，不跨进程——backend_workers > 1 时
并发写存在丢更新窗口。任务摘要是 advisory 数据（最坏丢一条上报），不作为
正确性依据；M4 迁移 SQLite 时一并解决。
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

#: 滚动上限：保留最近 N 条（bridge 上报为追加写，超出即截尾）
KAG_TASKS_LIMIT = 500

_lock = threading.Lock()


def _store_path() -> Path:
    from kagweb.multi_user.paths import SYSTEM_ROOT

    SYSTEM_ROOT.mkdir(parents=True, exist_ok=True)
    return SYSTEM_ROOT / "kag_tasks.json"


def _normalize(record: dict[str, Any]) -> dict[str, Any]:
    """A.3 契约字段的宽松归一（bridge 侧字段缺失不崩，列表页能渲染）。"""
    return {
        "task_id": str(record.get("task_id") or ""),
        "kind": str(record.get("kind") or "inference"),
        "session_id": str(record.get("session_id") or ""),
        "project_id": str(record.get("project_id") or ""),
        "namespace": str(record.get("namespace") or ""),
        "question": str(record.get("question") or "")[:2000],
        "answer_digest": str(record.get("answer_digest") or "")[:2000],
        "cost_ms": int(record.get("cost_ms") or 0),
        # P0a：build 记录可观测字段（可选，详情端点惰性回填，见 update_task）
        "scheduler_job_id": str(record.get("scheduler_job_id") or ""),
        "status": str(record.get("status") or ""),
        # 条数防御（评审 F5）：bridge 侧已截 20，此处再防异常巨大的上报体
        "references": (
            record.get("references")[:20] if isinstance(record.get("references"), list) else []
        ),
        "created_at": str(record.get("created_at") or time.strftime("%Y-%m-%dT%H:%M:%S%z")),
    }


def append_task(record: dict[str, Any]) -> dict[str, Any]:
    """追加上报记录（前插，滚动截断）。task_id 幂等：重复上报覆盖而非重复插入。"""
    normalized = _normalize(record)
    with _lock:
        path = _store_path()
        try:
            rows = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(rows, list):
                rows = []
        except (OSError, ValueError):
            rows = []
        rows = [r for r in rows if r.get("task_id") != normalized["task_id"]]
        rows.insert(0, normalized)
        rows = rows[:KAG_TASKS_LIMIT]
        path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    return normalized


def list_tasks(limit: int = 100, session_id: str = "", project_id: str = "") -> list[dict[str, Any]]:
    """列表（新在前），可按 session/project 过滤。存储缺失/损坏返回空列表。"""
    with _lock:
        try:
            rows = json.loads(_store_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
    if not isinstance(rows, list):
        return []
    if session_id:
        rows = [r for r in rows if r.get("session_id") == session_id]
    if project_id:
        rows = [r for r in rows if r.get("project_id") == project_id]
    return rows[: max(1, min(limit, KAG_TASKS_LIMIT))]


def update_task(task_id: str, **fields: Any) -> dict[str, Any] | None:
    """幂等更新已有记录的可观测字段（P0a live 状态回写/缓存回填）。

    白名单：scheduler_job_id / status / answer_digest / cost_ms；task_id
    不存在或存储缺失时返回 None（不新建、不报错——advisory 语义）。
    """
    if not task_id:
        return None
    allowed = {"scheduler_job_id", "status", "answer_digest", "cost_ms"}
    updates = {key: value for key, value in fields.items() if key in allowed}
    if not updates:
        return None
    with _lock:
        path = _store_path()
        try:
            rows = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(rows, list):
            return None
        for row in rows:
            if str(row.get("task_id") or "") == task_id:
                row.update(updates)
                path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
                return row
    return None
