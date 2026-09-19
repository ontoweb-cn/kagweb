# -*- coding: utf-8 -*-
"""KAG 管理面 REST（设计 §5.2/§6；M2 v1：项目 CRUD + Schema 只读 + 图概览 + 任务列表）。

两个 router：
- ``router``        —— 管理面端点（挂载时带 require_signed_in；写操作 admin-only）
- ``bridge_router`` —— Bridge 任务上报（附录 A.3；服务间 api_key 鉴权，无 JWT）

same-origin guard 沿用 settings router 的既有模式（origin_is_trusted）。
"""

from __future__ import annotations

import json
import re
from typing import Any

import httpx
from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel

from kagweb.services.kag import get_kag_settings, kag_enabled
from kagweb.services.kag.access import (
    derive_user_no,
    filter_projects_by_access,
    kag_read_allowed,
    project_access_allowed,
)
from kagweb.services.kag.member_store import (
    ensure_project_owner,
    project_members,
    project_owner,
    update_project_members,
)
from kagweb.services.kag.openspg_client import OpenSPGClient, OpenSPGError
from kagweb.services.kag.task_store import append_task, list_tasks

router = APIRouter()
bridge_router = APIRouter()

#: namespace 约束：Neo4j 数据库名仅纯字母数字（M0 实测，连字符/下划线均被拒）
_NAMESPACE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]{2,63}$")
#: userNo 约束：6-20 位字母/数字/下划线（ACCOUNT_PATTERN，M0-10 实测）
_USERNO_RE = re.compile(r"^[A-Za-z0-9_]{6,20}$")


def _current_user() -> Any:
    from kagweb.multi_user.context import get_current_user

    return get_current_user()


def _require_read() -> None:
    user = _current_user()
    if not kag_read_allowed(user, kag_configured=kag_enabled()):
        raise HTTPException(status_code=403, detail="KAG management plane is not available.")


def _require_project_access(project_id: str) -> None:
    """T2 项目级门禁（M4-B）：read 总门 + 该项目 membership。"""
    _require_read()
    user = _current_user()
    if not project_access_allowed(user, project_id, kag_configured=kag_enabled()):
        raise HTTPException(status_code=403, detail="You do not have access to this KAG project.")


def _require_admin() -> None:
    user = _current_user()
    if not (bool(getattr(user, "is_admin", False)) or getattr(user, "role", "user") == "admin"):
        raise HTTPException(status_code=403, detail="Admin only.")


def _require_same_origin(request: Request) -> None:
    from kagweb.services.config.origins import origin_is_trusted, request_authority
    from kagweb.services.config.runtime_settings import load_system_settings

    try:
        system = load_system_settings()
        allowed = [
            str(system.get("cors_origin") or ""),
            *(str(x) for x in (system.get("cors_origins") or [])),
        ]
    except Exception:
        allowed = []
    # 浏览器原始 host 优先（Next rewrite 会把 Host 改写为后端端口并追加
    # X-Forwarded-Host——同源判定用后者，见 origins.request_authority）
    if not origin_is_trusted(
        request.headers.get("origin"),
        request_authority(
            request.headers.get("host"), request.headers.get("x-forwarded-host")
        ),
        allowed,
    ):
        raise HTTPException(status_code=403, detail="Cross-site request refused.")


def _client() -> OpenSPGClient:
    block = get_kag_settings()
    return OpenSPGClient(str(block.get("spg_server_url") or ""))


#: 管理面响应剔除的凭据键（M2.4 冒烟发现：OpenSPG project.config 携带
#: vectorizer 的 api_key 与图存储密码，list/get 直接透传会向浏览器泄露
#: 服务端凭据）。沿模型目录 CATALOG_SECRET_MASK 的掩码形态。
_SECRET_KEYS = frozenset({"api_key", "apikey", "password", "token", "secret"})
#: 凭据后缀（评审 F3：与 grants.validate_grant 的 endswith("_key") 规则对齐，
#: 覆盖 user_password / auth_token 等复合键）。
_SECRET_SUFFIXES = ("_key", "_token", "_password", "_secret")
_SECRET_MASK = "***"


def _is_secret_key(key: Any) -> bool:
    """键名归一（小写、连字符→下划线）后判定：精确集或凭据后缀。

    覆盖 apiKey / api-key / user_password 等变体；"keyword" 一类普通词
    不含下划线、不以凭据后缀结尾，不误伤。
    """
    lowered = str(key).lower().replace("-", "_")
    return lowered in _SECRET_KEYS or lowered.endswith(_SECRET_SUFFIXES)


def _sanitize(value: Any) -> Any:
    """递归把凭据键的值替换为掩码（响应面向浏览器，凭据不出服务端）。

    OpenSPG 的 ``project.config`` 是**序列化 JSON 字符串**（M0-3 实测），
    字符串值尝试解析后掩码再原样序列化回字符串，保持字段形态不变。
    """
    if isinstance(value, dict):
        return {
            key: (_SECRET_MASK if _is_secret_key(key) else _sanitize(child))
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [_sanitize(item) for item in value]
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith("{") or stripped.startswith("["):
            try:
                parsed = json.loads(value)
            except ValueError:
                return value
            sanitized = _sanitize(parsed)
            if sanitized == parsed:
                return value
            return json.dumps(sanitized, ensure_ascii=False, separators=(",", ":"))
        return value
    return value


def _upstream_error(exc: OpenSPGError) -> HTTPException:
    detail = exc.body if isinstance(exc.body, str) and exc.body else str(exc)
    return HTTPException(status_code=502, detail=f"OpenSPG upstream error: {detail[:400]}")


# ---------------------------------------------------------------------------
# 项目
# ---------------------------------------------------------------------------


@router.get("/projects")
async def list_projects() -> dict[str, Any]:
    _require_read()
    try:
        projects = await _client().list_projects()
    except OpenSPGError as exc:
        raise _upstream_error(exc) from exc
    # T2（M4-B）：非 admin 仅返回其 owner/成员项目
    visible = filter_projects_by_access(
        _current_user(),
        [p for p in projects if isinstance(p, dict)],
        kag_configured=kag_enabled(),
    )
    return {"projects": [_sanitize(p) for p in visible]}


class KagProjectCreateRequest(BaseModel):
    """创建项目的请求体（评审 F4：pydantic 模型，contracts 生成线可产出 TS 类型）。"""

    name: str
    namespace: str
    # 模型目录 services.embedding.profiles 中的 profile id（凭据服务端取用）
    embedding_model_id: str = ""
    # 缺省时对 embedding 端点做一次实测探测（与 server 端校验同型）
    vector_dimensions: int | None = None
    service_user_no: str = ""


@router.post("/projects")
async def create_project(request: Request, payload: KagProjectCreateRequest) -> dict[str, Any]:
    """创建 LOCAL 项目（完整流程：vectorizer 从模型目录组装，维度经实测探测）。

    embedding_model_id 指向 KAGWeb 模型目录中的 embedding profile（凭据不回显）。
    T2（M4-B）：创建者成为项目 owner（登记 member_store），userNo 归因为
    ``derive_user_no(user_id)``（系统调用/本地无用户上下文回落 service_user_no）。
    """
    _require_read()
    _require_same_origin(request)

    name = str(payload.name or "").strip()
    namespace = str(payload.namespace or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    if not _NAMESPACE_RE.match(namespace):
        raise HTTPException(
            status_code=400,
            detail="namespace 必须为纯字母数字且以字母开头（Neo4j 数据库名约束，3-64 位）",
        )
    user = _current_user()
    uid = str(getattr(user, "user_id", "") or str(getattr(user, "id", "") or "") or "")
    if uid:
        user_no = derive_user_no(uid)
    else:
        block = get_kag_settings()
        user_no = str(payload.service_user_no or block.get("service_user_no") or "kagweb")
        if not _USERNO_RE.match(user_no):
            raise HTTPException(status_code=400, detail="service_user_no 须 6-20 位字母/数字/下划线")

    vectorizer = await _assemble_vectorizer(
        model_id=str(payload.embedding_model_id or "").strip(),
        vector_dimensions=payload.vector_dimensions,
    )
    try:
        result = await _client().create_project(
            name=name, namespace=namespace, user_no=user_no, vectorizer=vectorizer
        )
    except OpenSPGError as exc:
        raise _upstream_error(exc) from exc
    project = result if isinstance(result, dict) else {}
    project_id = str(project.get("projectId") or project.get("id") or "")
    if project_id and uid:
        ensure_project_owner(project_id, uid)
    return {"project": _sanitize(project)}


async def _assemble_vectorizer(*, model_id: str, vector_dimensions: int | None) -> dict[str, Any]:
    """从模型目录的 embedding profile 组装 vectorizer（M0-10 实测契约）。

    维度优先用请求值；缺省时对 embedding 端点做一次实测探测（一次
    /embeddings 调用，与 server 端 pemja 校验同型——避免错配维度）。
    凭据只在服务端流转，不回显。
    """
    connection = _embedding_connection(model_id)
    if connection is None:
        raise HTTPException(
            status_code=400,
            detail="embedding_model_id 未在模型目录的 embedding 服务中配置",
        )
    vectorizer = {
        "type": "openai",
        "api_key": str(connection.get("api_key") or ""),
        "base_url": str(connection.get("base_url") or ""),
        "model": str(connection.get("model") or model_id),
    }
    if isinstance(vector_dimensions, int) and vector_dimensions > 0:
        vectorizer["vector_dimensions"] = vector_dimensions
    else:
        vectorizer["vector_dimensions"] = await _probe_dimensions(vectorizer)
    return vectorizer


def _embedding_connection(model_id: str) -> dict[str, Any] | None:
    """模型目录中 embedding profile 的连接信息（api_key/base_url/model）。

    目录真实形态（评审 F2 修正）：``services.embedding.profiles[]``，
    profile 自含 base_url/api_key（connection 凭据已在保存时镜像下来）。
    凭据只在服务端流转，不回显。
    """
    from kagweb.services.config.model_catalog import get_model_catalog_service

    try:
        catalog = get_model_catalog_service().load()
    except Exception:
        return None
    services = catalog.get("services") if isinstance(catalog, dict) else {}
    embedding = services.get("embedding") if isinstance(services, dict) else {}
    for profile in (embedding or {}).get("profiles", []) or []:
        if not isinstance(profile, dict) or str(profile.get("id") or "") != model_id:
            continue
        # KAGWeb 目录约定（_CONNECTION_BASE_SUFFIX）：embedding profile 的
        # base_url 存完整端点（含 /embeddings）；KAG 的 OpenAIVectorizeModel
        # 期望 OpenAI API base（客户端自己拼 /embeddings）——组装时转换
        base_url = str(profile.get("base_url") or "").rstrip("/")
        if base_url.endswith("/embeddings"):
            base_url = base_url[: -len("/embeddings")]
        return {
            "api_key": str(profile.get("api_key") or ""),
            "base_url": base_url,
            "model": str(profile.get("model") or profile.get("name") or model_id),
        }
    return None


async def _probe_dimensions(vectorizer: dict[str, Any]) -> int:
    """一次 /embeddings 实测探测输出维度（与 server 端校验同型，~数百 ms）。"""
    url = str(vectorizer["base_url"]).rstrip("/")
    if not url:
        raise HTTPException(status_code=400, detail="embedding 模型缺 base_url，无法探测维度")
    if not url.endswith("/embeddings"):
        url = f"{url}/embeddings"
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(
                url,
                headers={"Authorization": f"Bearer {vectorizer['api_key']}"},
                json={"model": vectorizer["model"], "input": "dimension probe"},
            )
        resp.raise_for_status()
        data = resp.json()
        return len(data["data"][0]["embedding"])
    except Exception as exc:
        raise HTTPException(
            status_code=400, detail=f"embedding 维度探测失败：{exc!r}"
        ) from exc


@router.get("/projects/{project_id}")
async def get_project(project_id: str) -> dict[str, Any]:
    """项目详情 + Schema 摘要 + 图 labels 概览（labels 失败不阻塞详情）。"""
    _require_project_access(project_id)
    client = _client()
    try:
        project = await client.get_project(project_id)
    except OpenSPGError as exc:
        raise _upstream_error(exc) from exc
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    project = _sanitize(project)
    schema_summary: Any = None
    labels: Any = None
    try:
        schema = await client.query_schema(project_id)
        types = schema.get("spgTypes") or []
        schema_summary = {
            "spg_type_count": len(types),
            "spg_type_names": [
                str(((t.get("basicInfo") or {}).get("name") or {}).get("name") or "")
                for t in types
                if isinstance(t, dict)
            ][:100],
        }
    except OpenSPGError:
        schema_summary = None
    try:
        labels = await client.all_labels(project_id)
    except OpenSPGError:
        labels = None  # M2 侦察修正：graph 仅有 allLabels，失败降级为空
    return {"project": project, "schema_summary": schema_summary, "graph_labels": labels}


@router.get("/projects/{project_id}/schema")
async def get_project_schema(project_id: str) -> dict[str, Any]:
    _require_project_access(project_id)
    try:
        schema = await _client().query_schema(project_id)
    except OpenSPGError as exc:
        raise _upstream_error(exc) from exc
    return {"schema": schema}


class KagSchemaRelationAdd(BaseModel):
    """新增关系（M3.5）：目标类型用 nameEn（schema 内现有类型）。"""

    name: str
    name_zh: str = ""
    desc: str = ""
    object_type_name: str


class KagSchemaEditRequest(BaseModel):
    """Schema 编辑（M3.5）：读模型进、wire 转换在后端。

    ``spg_type`` 为 queryProjectSchema 返回的 SPG type 原样（前端就地编辑
    中文名/描述等）；新增/删除关系以意图列表表达（CREATE/DELETE 元素级
    操作由服务端组装——M3.5 实测 wire 契约，schema_draft.py）。
    """

    spg_type: dict[str, Any]
    add_relations: list[KagSchemaRelationAdd] = []
    delete_relations: list[str] = []


@router.post("/projects/{project_id}/schema/alter")
async def alter_project_schema(
    request: Request, project_id: str, payload: KagSchemaEditRequest
) -> dict[str, Any]:
    """提交 Schema 变更（项目成员 + same-origin；M3.5）。

    语义（M3.5 实测）：UPDATE 覆写 + 元素级 CREATE/DELETE；缺条目不等于
    删除（服务端 500），删除必须显式 DELETE 操作。T2（M4-B）写操作由
    admin-only 放宽为项目 membership（owner/成员可改自己的项目）。
    """
    _require_project_access(project_id)
    _require_same_origin(request)
    from kagweb.services.kag.schema_draft import new_relation, read_type_to_draft

    spg_type = payload.spg_type
    name = ((spg_type.get("basicInfo") or {}).get("name") or {})
    if not str(name.get("nameEn") or "").strip():
        raise HTTPException(status_code=400, detail="spg_type.basicInfo.name.nameEn is required")
    if not payload.add_relations and not payload.delete_relations:
        raise HTTPException(status_code=400, detail="nothing to alter")
    # 组装：读模型 → wire draft
    draft = read_type_to_draft(spg_type)
    if payload.add_relations:
        try:
            schema = await _client().query_schema(project_id)
        except OpenSPGError as exc:
            raise _upstream_error(exc) from exc
        types = schema.get("spgTypes") or []
        for add in payload.add_relations:
            target = next(
                (
                    t
                    for t in types
                    if ((t.get("basicInfo") or {}).get("name") or {}).get("nameEn") == add.object_type_name
                ),
                None,
            )
            if target is None:
                raise HTTPException(
                    status_code=400, detail=f"object type not found: {add.object_type_name}"
                )
            if not str(add.name or "").strip():
                raise HTTPException(status_code=400, detail="relation name is required")
            draft.setdefault("relations", []).append(
                new_relation(
                    object_type=target,
                    name=str(add.name).strip(),
                    name_zh=str(add.name_zh or ""),
                    desc=str(add.desc or ""),
                )
            )
    if payload.delete_relations:
        doomed = {str(n) for n in payload.delete_relations}
        existing = {
            str(((r.get("basicInfo") or {}).get("name") or {}).get("name") or "")
            for r in (draft.get("relations") or [])
        }
        unknown = doomed - existing
        if unknown:
            raise HTTPException(
                status_code=400, detail=f"relations not found: {sorted(unknown)}"
            )
        for rel in draft.get("relations") or []:
            if str(((rel.get("basicInfo") or {}).get("name") or {}).get("name") or "") in doomed:
                rel["alterOperation"] = "DELETE"
    try:
        result = await _client().alter_schema(project_id, [draft])
    except OpenSPGError as exc:
        raise _upstream_error(exc) from exc
    # 跨请求回读验证（防 knext 式"进程内缓存假阳性"在代理层复现）：alter 后
    # 立即 query 一次，确认变更已在服务端可见。
    try:
        after = await _client().query_schema(project_id)
    except OpenSPGError:
        after = None
    return {"result": result, "schema_after": after}


class KagMembersUpdateRequest(BaseModel):
    """项目成员设置（T2，M4-B）：owner 不变，members 为成员 uid 列表。"""

    members: list[str] = []


@router.get("/projects/{project_id}/members")
async def get_project_members(project_id: str) -> dict[str, Any]:
    """项目成员（项目访问可读；T2）。``can_edit`` 标记当前用户是否可改
    成员（admin），供前端控制编辑区显隐。"""
    from kagweb.services.kag.access import _is_admin

    _require_project_access(project_id)
    owner = project_owner(project_id)
    user = _current_user()
    return {
        "owner": owner,
        "owner_user_no": derive_user_no(owner) if owner else "",
        "members": project_members(project_id),
        "can_edit": _is_admin(user),
    }


@router.put("/projects/{project_id}/members")
async def put_project_members(
    request: Request, project_id: str, payload: KagMembersUpdateRequest
) -> dict[str, Any]:
    """设置项目成员（admin + same-origin；T2，M4-B）。"""
    _require_admin()
    _require_same_origin(request)
    try:
        entry = update_project_members(project_id, [str(m) for m in payload.members])
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"owner": entry.get("owner", ""), "members": entry.get("members", [])}


class KagGraphQueryRequest(BaseModel):
    """图浏览 DSL 查询（M3.4，经 /public/v1/reason/run）。

    dsl 契约（M3.0/M3.3 实测）：节点类型须 namespace 全名（如
    m0ProbeLive.Person），关系 label 裸名（如 workFor）；params 值须
    字符串化列表。
    """

    dsl: str
    params: dict[str, str] = {}


_MAX_GRAPH_ROWS = 200


@router.post("/projects/{project_id}/graph/query")
async def query_project_graph(
    request: Request, project_id: str, payload: KagGraphQueryRequest
) -> dict[str, Any]:
    """图浏览 DSL 查询（read 权限 + same-origin；M3.4）——rows ≤200 裁剪。

    same-origin 与其他 POST 端点一致（评审 M-3，纵深防御：跨站无法读
    响应，但保持写类端点的统一门禁面）。T2（M4-B）项目级门禁。
    """
    _require_project_access(project_id)
    _require_same_origin(request)
    dsl = str(payload.dsl or "").strip()
    if not dsl:
        raise HTTPException(status_code=400, detail="dsl is required")
    try:
        resp = await _client().reason_run(project_id, dsl, payload.params)
    except OpenSPGError as exc:
        raise _upstream_error(exc) from exc
    task = resp.get("task") or {}
    status = str(task.get("status") or "")
    table = task.get("resultTableResult") or {}
    rows = table.get("rows") or []
    out: dict[str, Any] = {
        "status": status or "UNKNOWN",
        "header": list(table.get("header") or []),
        "rows": rows[:_MAX_GRAPH_ROWS],
        "row_count": int(table.get("total") or len(rows)),
        "truncated": len(rows) > _MAX_GRAPH_ROWS,
    }
    if status != "FINISH":
        detail = str(task.get("resultMessage") or f"task not finished: {status}")
        out["error"] = detail[:600]
    return out


class KagBuildRequest(BaseModel):
    """触发 KAG_COMMAND 构建（M4-A）。command 为 server 侧执行的构建命令。"""

    command: str
    worker_num: int = 1


@router.post("/projects/{project_id}/build")
async def submit_build(
    request: Request, project_id: str, payload: KagBuildRequest
) -> dict[str, Any]:
    """提交构建任务（项目成员 + same-origin；M4-A）。

    受理层交付：经 ``/public/v1/builder/kag/submit`` 提交并登记 task_store
    （kind=build）。奇偶性：本地 compose 的 computing engine 未配置，任务
    受理 RUNNING 但执行会失败（设计风险 #5）——端到端执行依赖 M5 上游化。
    """
    _require_project_access(project_id)
    _require_same_origin(request)
    command = str(payload.command or "").strip()
    if not command:
        raise HTTPException(status_code=400, detail="command is required")
    try:
        resp = await _client().submit_builder(project_id, command, payload.worker_num)
    except OpenSPGError as exc:
        raise _upstream_error(exc) from exc
    result = resp.get("result") or {}
    job_id = str(result.get("id") or "") or str(result.get("jobName") or "")
    record = append_task(
        {
            "task_id": job_id or f"build_{int(__import__('time').time() * 1000)}",
            "kind": "build",
            "project_id": project_id,
            "question": command[:2000],
            "answer_digest": f"status={result.get('status') or 'UNKNOWN'}",
            "cost_ms": 0,
        }
    )
    return {"build_job": result, "task": record}


# ---------------------------------------------------------------------------
# 推理任务（自有存储；Bridge 上报见 bridge_router）
# ---------------------------------------------------------------------------


@router.get("/tasks")
async def get_tasks(
    limit: int = 100, session_id: str = "", project_id: str = ""
) -> dict[str, Any]:
    """推理任务列表（T2，M4-B 评审 M-7）：admin 全量；非 admin 仅其
    membership 可见项目的任务（防跨项目窥探 question/answer/references）。"""
    from kagweb.services.kag.access import _is_admin

    _require_read()
    user = _current_user()
    rows = list_tasks(limit=limit, session_id=session_id, project_id=project_id)
    if project_id:
        # 显式 project 过滤：校验该项目的 membership（admin 恒过）
        if not project_access_allowed(user, project_id, kag_configured=kag_enabled()):
            raise HTTPException(status_code=403, detail="You do not have access to this KAG project.")
        return {"tasks": rows, "count": len(rows)}
    if not _is_admin(user):
        # 无 project 过滤（全量浏览）：仅保留用户可见项目的任务
        visible = {
            str(p.get("projectId") or p.get("id") or "")
            for p in filter_projects_by_access(
                user,
                await _client().list_projects(),
                kag_configured=kag_enabled(),
            )
        }
        rows = [r for r in rows if not r.get("project_id") or str(r.get("project_id")) in visible]
    return {"tasks": rows, "count": len(rows)}


@bridge_router.post("/tasks")
async def report_bridge_task(
    payload: dict[str, Any],
    x_kag_bridge_key: str = Header(default="", alias="X-KAG-Bridge-Key"),
) -> dict[str, Any]:
    """Bridge 任务上报（附录 A.3）：bridge_api_key 鉴权（常量时间比较）。"""
    import hmac

    expected = str(get_kag_settings().get("bridge_api_key") or "")
    if not expected or not hmac.compare_digest(expected, str(x_kag_bridge_key or "")):
        raise HTTPException(status_code=401, detail="invalid bridge key")
    record = append_task(payload)
    return {"ok": True, "task_id": record.get("task_id")}
