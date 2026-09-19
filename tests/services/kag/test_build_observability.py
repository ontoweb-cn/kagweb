# -*- coding: utf-8 -*-
"""P0a 构建任务可观测性测试：OpenSPG 可观测 client（MockTransport）、
task_store.update_task、路由（列表 live 合并 / 详情组装 / ACL 403 / 降级）。

契约基线：A1.4 实测归档（docs/plans/2026-09-19-kag-ui-observability-plan.md）——
builder/scheduler 走 execute2 信封 {result: ...}；BuilderJob.status 恒 RUNNING；
成败判定取节点级（taskDag.nodes[].properties.status / SchedulerTask.status）聚合。
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from kagweb.services.kag.openspg_client import OpenSPGClient, OpenSPGError


# ---------------------------------------------------------------------------
# 可观测 client（httpx.MockTransport，无网络）
# ---------------------------------------------------------------------------


def _mock_client(handler) -> OpenSPGClient:
    return OpenSPGClient("http://spg.test", transport=httpx.MockTransport(handler))


def _envelope(payload: object) -> str:
    return json.dumps({"result": payload})


@pytest.mark.asyncio
async def test_get_builder_job_unwraps_envelope() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json={"result": {"id": 2, "taskId": 22, "status": "RUNNING"}})

    job = await _mock_client(handler).get_builder_job(2)
    assert job == {"id": 2, "taskId": 22, "status": "RUNNING"}
    assert seen == ["http://spg.test/public/v1/builder/getById?id=2"]


@pytest.mark.asyncio
async def test_get_builder_job_degraded_returns_empty() -> None:
    client = _mock_client(lambda request: httpx.Response(200, text="boom"))
    assert await client.get_builder_job(2) == {}


@pytest.mark.asyncio
async def test_search_builder_jobs_unwraps_results() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={"result": {"results": [{"id": 1}], "pageIdx": 1, "pageSize": 100, "total": 1}},
        )

    rows = await _mock_client(handler).search_builder_jobs(3)
    assert rows == [{"id": 1}]
    assert captured["body"] == {"projectId": 3, "pageNo": 1, "pageSize": 100}


@pytest.mark.asyncio
async def test_search_scheduler_instances_and_tasks_unwrap() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "/instance/search" in str(request.url):
            return httpx.Response(200, json={"result": {"results": [{"id": 222}]}})
        return httpx.Response(200, json={"result": {"results": [{"id": 3, "status": "ERROR"}]}})

    client = _mock_client(handler)
    instances = await client.search_scheduler_instances(22)
    assert instances == [{"id": 222}]
    tasks = await client.search_scheduler_tasks(222)
    assert tasks == [{"id": 3, "status": "ERROR"}]


@pytest.mark.asyncio
async def test_search_unwrap_degraded_empty() -> None:
    client = _mock_client(lambda request: httpx.Response(200, json={"result": "not-a-dict"}))
    assert await client.search_builder_jobs(3) == []
    assert await client.search_scheduler_instances(22) == []
    assert await client.search_scheduler_tasks(222) == []


# ---------------------------------------------------------------------------
# task_store.update_task（SYSTEM_ROOT 重定向到 tmp）
# ---------------------------------------------------------------------------


@pytest.fixture()
def _task_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    import kagweb.multi_user.paths as paths_mod
    import kagweb.services.kag.task_store as store_mod

    monkeypatch.setattr(paths_mod, "SYSTEM_ROOT", tmp_path)
    monkeypatch.setattr(store_mod, "_store_path", lambda: tmp_path / "kag_tasks.json")
    return tmp_path


def test_update_task_updates_allowed_fields(_task_root: Path) -> None:
    from kagweb.services.kag.task_store import append_task, list_tasks, update_task

    append_task({"task_id": "2", "kind": "build", "project_id": "3"})
    updated = update_task("2", scheduler_job_id="22", status="RUNNING")
    assert updated is not None
    assert updated["scheduler_job_id"] == "22"
    row = list_tasks()[0]
    assert row["scheduler_job_id"] == "22"
    assert row["status"] == "RUNNING"


def test_update_task_whitelist_and_unknown(_task_root: Path) -> None:
    from kagweb.services.kag.task_store import append_task, update_task

    append_task({"task_id": "2", "kind": "build"})
    assert update_task("2", question="nope") is None  # 白名单外字段不写
    assert update_task("missing") is None  # 不存在 → None


def test_update_task_corrupt_store_returns_none(_task_root: Path) -> None:
    from kagweb.services.kag.task_store import update_task

    (_task_root / "kag_tasks.json").write_text("not json", encoding="utf-8")
    assert update_task("2", status="failed") is None


# ---------------------------------------------------------------------------
# 状态归一（A1.4 口径：节点级聚合）
# ---------------------------------------------------------------------------


def test_map_legacy_status() -> None:
    from kagweb.api.routers.kag import _map_legacy_status

    assert _map_legacy_status("ERROR") == "failed"
    assert _map_legacy_status("FAILED") == "failed"
    assert _map_legacy_status("FINISH") == "success"
    assert _map_legacy_status("SUCCESS") == "success"
    assert _map_legacy_status("RUNNING") == "running"
    assert _map_legacy_status("WAIT") == "pending"
    assert _map_legacy_status("WAITING") == "pending"
    assert _map_legacy_status(None) == "pending"


def test_aggregate_build_status_node_level() -> None:
    from kagweb.api.routers.kag import _aggregate_build_status

    def instance(*node_statuses: str, instance_status: str = "WAITING") -> dict:
        return {
            "status": instance_status,
            "taskDag": {
                "nodes": [
                    {"name": f"n{i}", "properties": {"status": s}} for i, s in enumerate(node_statuses)
                ],
                "edges": [],
            },
        }

    # 任一节点 ERROR → failed（A1.4 实测：实例级仍 WAITING，不能据此判成败）
    assert _aggregate_build_status(instance("ERROR", "WAIT")) == "failed"
    assert _aggregate_build_status(instance("FINISH", "FINISH")) == "success"
    assert _aggregate_build_status(instance("RUNNING", "WAIT")) == "running"
    assert _aggregate_build_status(instance("WAIT", "WAIT")) == "pending"
    # 无 taskDag → 兜底实例级 status
    assert _aggregate_build_status({"status": "RUNNING"}) == "running"
    assert _aggregate_build_status({"status": "WAITING"}) == "pending"
    assert _aggregate_build_status({}) == "pending"


# ---------------------------------------------------------------------------
# 路由级（FastAPI TestClient；_client 注入 FakeOpenSPG）
# ---------------------------------------------------------------------------

# A1.4 实测形态样本
JOB_ERROR = {
    "id": 2,
    "projectId": 3,
    "taskId": 22,
    "jobName": "KAG_COMMAND_164072_20260919163737",
    "status": "RUNNING",  # 恒 RUNNING，不随执行更新
    "type": "KAG_COMMAND",
    "version": "V3",
    "computingConf": json.dumps({"command": "echo m4-build-probe"}),
}
INSTANCE_FAILED = {
    "id": 222,
    "jobId": 22,
    "projectId": 3,
    "status": "WAITING",
    "taskDag": {
        "nodes": [
            {
                "name": "Builder",
                "taskComponent": "computingEngineAsyncTask",
                "properties": {"status": "ERROR", "executeNum": 3},
            },
            {
                "name": "PostProcessor",
                "taskComponent": "kagCommandPostSyncTask",
                "properties": {"status": "WAIT"},
            },
        ],
        "edges": [],
    },
}
TASK_FAILED = [
    {
        "id": 3,
        "title": "Builder",
        "type": "computingEngineAsyncTask",
        "status": "ERROR",
        "traceLog": "\ncannot find driver for\n" + "x" * 3000,
    },
    {
        "id": 4,
        "title": "PostProcessor",
        "type": "kagCommandPostSyncTask",
        "status": "WAIT",
        "traceLog": "\n",
    },
]


class FakeOpenSPG:
    """_client() 注入替身：按方法名注入 OpenSPGError 模拟上游不可达。"""

    def __init__(self, jobs=None, instances=None, tasks=None, errors=()):
        self.jobs = {str(k): v for k, v in (jobs or {}).items()}
        self.instances = {str(k): v for k, v in (instances or {}).items()}
        self.tasks = {str(k): v for k, v in (tasks or {}).items()}
        self.errors = set(errors)

    async def get_builder_job(self, job_id):
        if "get_builder_job" in self.errors:
            raise OpenSPGError("boom")
        return self.jobs.get(str(job_id), {})

    async def search_builder_jobs(self, project_id, page_size=100):
        return []

    async def search_scheduler_instances(self, job_id, page_size=5):
        if "search_scheduler_instances" in self.errors:
            raise OpenSPGError("boom")
        return self.instances.get(str(job_id), [])

    async def search_scheduler_tasks(self, instance_id, page_size=50):
        if "search_scheduler_tasks" in self.errors:
            raise OpenSPGError("boom")
        return self.tasks.get(str(instance_id), [])


def _patch_router_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """先把 store 根与文件路径重定向到 tmp，再 seed/起 client（防污染真实 data/）。"""
    import kagweb.multi_user.paths as paths_mod
    import kagweb.services.kag.task_store as store_mod

    monkeypatch.setattr(paths_mod, "SYSTEM_ROOT", tmp_path)
    monkeypatch.setattr(store_mod, "_store_path", lambda: tmp_path / "kag_tasks.json")


def _router_client(monkeypatch: pytest.MonkeyPatch, fake: FakeOpenSPG, user: SimpleNamespace):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from kagweb.api.routers.kag import router

    monkeypatch.setattr("kagweb.api.routers.kag._client", lambda: fake)
    monkeypatch.setattr("kagweb.api.routers.kag.kag_enabled", lambda: True)
    monkeypatch.setattr("kagweb.api.routers.kag._current_user", lambda: user)

    app = FastAPI()
    app.include_router(router, prefix="/api/kag")
    return TestClient(app)


_ADMIN = SimpleNamespace(user_id="admin", role="admin", is_admin=True)
_OUTSIDER = SimpleNamespace(user_id="eve", role="user", is_admin=False)


def test_list_project_builds_merges_live_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_router_env(monkeypatch, tmp_path)
    from kagweb.services.kag.task_store import append_task

    append_task({"task_id": "2", "kind": "build", "project_id": "3", "question": "echo x"})
    append_task({"task_id": "i1", "kind": "inference", "project_id": "3", "question": "q"})

    fake = FakeOpenSPG(jobs={"2": JOB_ERROR}, instances={"22": [INSTANCE_FAILED]})
    client = _router_client(monkeypatch, fake, _ADMIN)
    resp = client.get("/api/kag/projects/3/builds")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["count"] == 1  # 只含 build 记录
    build = body["builds"][0]
    assert build["task_id"] == "2"
    assert build["live_status"] == "failed"  # 节点级聚合（instance 级仍 WAITING）


def test_list_project_builds_membership_and_degraded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_router_env(monkeypatch, tmp_path)
    from kagweb.services.kag.task_store import append_task

    append_task({"task_id": "2", "kind": "build", "project_id": "3"})

    # 非成员 → 403
    outsider = _router_client(monkeypatch, FakeOpenSPG(jobs={"2": JOB_ERROR}), _OUTSIDER)
    assert outsider.get("/api/kag/projects/3/builds").status_code == 403

    # 上游不可达 → live_status=unknown，不 500
    degraded = FakeOpenSPG(errors={"get_builder_job"})
    client = _router_client(monkeypatch, degraded, _ADMIN)
    resp = client.get("/api/kag/projects/3/builds")
    assert resp.status_code == 200, resp.text
    assert resp.json()["builds"][0]["live_status"] == "unknown"


def test_build_detail_assembles_nodes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_router_env(monkeypatch, tmp_path)
    fake = FakeOpenSPG(
        jobs={"2": JOB_ERROR},
        instances={"22": [INSTANCE_FAILED]},
        tasks={"222": TASK_FAILED},
    )
    client = _router_client(monkeypatch, fake, _ADMIN)
    resp = client.get("/api/kag/builds/2")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["live_status"] == "failed"
    builder, post = body["nodes"]
    # traceLog 只读截断 2k（A1.4 实测：失败任务被反复重试、traceLog 持续追加）；
    # 3022 字符的原始日志 → 前 2000 字符
    assert builder["name"] == "Builder"
    assert builder["type"] == "computingEngineAsyncTask"
    assert builder["status"] == "failed"
    assert builder["trace_log"] == "\ncannot find driver for\n" + "x" * 1976
    assert post["status"] == "pending"
    # job 响应过凭据掩码（_sanitize）
    assert body["job"]["id"] == 2


def test_build_detail_falls_back_to_taskdag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_router_env(monkeypatch, tmp_path)
    fake = FakeOpenSPG(jobs={"2": JOB_ERROR}, instances={"22": [INSTANCE_FAILED]})
    client = _router_client(monkeypatch, fake, _ADMIN)
    resp = client.get("/api/kag/builds/2")
    assert resp.status_code == 200, resp.text
    nodes = resp.json()["nodes"]
    assert [n["name"] for n in nodes] == ["Builder", "PostProcessor"]
    assert nodes[0]["status"] == "failed"
    assert nodes[0]["trace_log"] == ""  # 无 task 记录时 taskDag 兜底、无日志


def test_build_detail_404_and_403(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_router_env(monkeypatch, tmp_path)
    # 未知 job → 404
    client = _router_client(monkeypatch, FakeOpenSPG(), _ADMIN)
    assert client.get("/api/kag/builds/999").status_code == 404
    # 非成员（job 归属 project 3）→ 403
    fake = FakeOpenSPG(jobs={"2": JOB_ERROR})
    outsider = _router_client(monkeypatch, fake, _OUTSIDER)
    assert outsider.get("/api/kag/builds/2").status_code == 403


def test_get_tasks_live_merge_build_rows_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_router_env(monkeypatch, tmp_path)
    from kagweb.services.kag.task_store import append_task

    append_task({"task_id": "2", "kind": "build", "project_id": "3", "question": "echo x"})
    append_task({"task_id": "i1", "kind": "inference", "project_id": "3", "question": "q"})

    fake = FakeOpenSPG(jobs={"2": JOB_ERROR}, instances={"22": [INSTANCE_FAILED]})
    client = _router_client(monkeypatch, fake, _ADMIN)
    resp = client.get("/api/kag/tasks")
    assert resp.status_code == 200, resp.text
    by_id = {t["task_id"]: t for t in resp.json()["tasks"]}
    assert by_id["2"]["live_status"] == "failed"
    assert "live_status" not in by_id["i1"]  # inference 行不受影响（评审 P2-4）


def test_get_tasks_live_merge_degraded_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_router_env(monkeypatch, tmp_path)
    from kagweb.services.kag.task_store import append_task

    append_task({"task_id": "2", "kind": "build", "project_id": "3"})
    client = _router_client(
        monkeypatch, FakeOpenSPG(errors={"get_builder_job"}), _ADMIN
    )
    resp = client.get("/api/kag/tasks")
    assert resp.status_code == 200, resp.text
    assert resp.json()["tasks"][0]["live_status"] == "unknown"


def test_get_tasks_non_numeric_build_task_id_degrades_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """评审 P1 回归：submit_build 兜底 task_id（build_<ts>）非数字，
    不得 int() ValueError 拖垮列表页——降级 unknown 而非 500。"""
    _patch_router_env(monkeypatch, tmp_path)
    from kagweb.services.kag.task_store import append_task

    append_task({"task_id": "build_1727000000000", "kind": "build", "project_id": "3"})
    client = _router_client(monkeypatch, FakeOpenSPG(), _ADMIN)
    resp = client.get("/api/kag/tasks")
    assert resp.status_code == 200, resp.text
    assert resp.json()["tasks"][0]["live_status"] == "unknown"


def test_build_detail_non_numeric_job_id_404(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """评审 P1 回归：非数字 job_id 直接 404（OpenSPG BuilderJob id 必为数字）。"""
    _patch_router_env(monkeypatch, tmp_path)
    client = _router_client(monkeypatch, FakeOpenSPG(), _ADMIN)
    assert client.get("/api/kag/builds/abc").status_code == 404
    assert client.get("/api/kag/builds/1.5").status_code == 404
