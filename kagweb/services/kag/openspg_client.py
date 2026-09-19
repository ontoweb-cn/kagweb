# -*- coding: utf-8 -*-
"""OpenSPG server 原生 REST 客户端（/public/v1/*，设计 §5.2）。

契约基线：M0 实测归档（scripts/kag_m0/results/m0_3、m0_4）+ knext 客户端模型
（开源权威参考）。鉴权前提：/public/* 无认证、信任调用方（§3.2/§6.1），
server 仅内网部署。创建项目走完整 LOCAL 流程：config.vectorizer 必需且会被
server 端 pemja 真实验证（M0-10 实测）。
"""

from __future__ import annotations

import json
from typing import Any

import httpx


class OpenSPGError(RuntimeError):
    """上游 OpenSPG 调用失败（4xx/5xx 或网络错误）。"""

    def __init__(self, message: str, *, status: int = 0, body: str = ""):
        super().__init__(message)
        self.status = status
        self.body = body


def _parse_response(resp: httpx.Response) -> Any:
    """server 的成功体是 JSON；错误体常见为裸字符串（M0-4 实测）。"""
    text = resp.text or ""
    try:
        return json.loads(text)
    except ValueError:
        return text


class OpenSPGClient:
    """轻量异步封装：每方法一次请求、无内部重试（管理面代理语义）。

    实例廉价（每请求建连），由调用方按请求构造——与 httpx 的推荐用法一致。
    """

    def __init__(self, base_url: str, timeout: float = 30.0, transport: Any = None):
        self._base = (base_url or "").rstrip("/")
        self._timeout = timeout
        # transport 仅测试注入（httpx.MockTransport）；生产为 None
        self._transport = transport

    def _url(self, path: str) -> str:
        if not self._base:
            raise OpenSPGError("kag settings 域未配置 spg_server_url")
        return f"{self._base}{path}"

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> Any:
        url = self._url(path)
        try:
            async with httpx.AsyncClient(
                timeout=timeout or self._timeout, transport=self._transport
            ) as client:
                resp = await client.request(method, url, params=params, json=json_body)
        except httpx.HTTPError as exc:
            raise OpenSPGError(f"OpenSPG {method} {path} 网络错误：{exc!r}") from exc
        if resp.status_code >= 400:
            raise OpenSPGError(
                f"OpenSPG {method} {path} -> HTTP {resp.status_code}",
                status=resp.status_code,
                body=(resp.text or "")[:500],
            )
        return _parse_response(resp)

    # —— 项目（ProjectController，/public/v1/project）——

    async def list_projects(self) -> list[dict[str, Any]]:
        # 类型防御（评审 F6）：server 偶发返回空体/字符串，统一归一为列表
        result = await self._request("GET", "/public/v1/project")
        return result if isinstance(result, list) else []

    async def get_project(self, project_id: str | int) -> dict[str, Any] | None:
        result = await self._request(
            "GET", "/public/v1/project", params={"projectId": int(project_id)}
        )
        if isinstance(result, list):
            return result[0] if result else None
        return result if isinstance(result, dict) else None

    async def create_project(
        self,
        *,
        name: str,
        namespace: str,
        user_no: str,
        vectorizer: dict[str, Any],
        auto_schema: bool = True,
    ) -> Any:
        """创建 LOCAL 项目（完整流程：vectorizer 经 server 端 pemja 真实验证）。

        namespace 约束（M0 实测）：Neo4j 数据库名仅接受纯字母数字
        （下划线/连字符均被拒），且 userNo 须 6-20 位字母/数字/下划线。
        """
        return await self._request(
            "POST",
            "/public/v1/project",
            json_body={
                "name": name,
                "namespace": namespace,
                # M0-10 实测契约：tag=LOCAL 时 config.vectorizer 必需
                "config": {"vectorizer": vectorizer},
                "visibility": "PRIVATE",
                "tag": "LOCAL",
                "userNo": user_no,
                "autoSchema": auto_schema,
            },
            # 建项目含 pemja 向量验证 + Neo4j 建库，受限内存环境可达数分钟（M0 实测）
            timeout=300.0,
        )

    # update_project 刻意未实现（评审 F3）：server 端 /update 校验要求 config
    # 必填（ProjectController L159），裸 update 必 400；M3 表单编辑落地时随
    # 完整 config 一起提供。

    # —— Schema（SchemaController，/public/v1/schema）——

    async def query_schema(self, project_id: str | int) -> dict[str, Any]:
        """项目 Schema（spgTypes 列表）；路径为 M0-4 实测命中的候选 A。"""
        result = await self._request(
            "GET", "/public/v1/schema/queryProjectSchema", params={"projectId": int(project_id)}
        )
        return result if isinstance(result, dict) else {"spgTypes": result or []}

    async def alter_schema(
        self, project_id: str | int, schema_draft: list[dict[str, Any]]
    ) -> Any:
        """提交 Schema 变更（M3.5）。

        REST wire 契约（M3.5 实测：拦截 knext ``schema_alter_schema_post`` 抓
        ``sanitize_for_serialization`` 产物并重放 200）：POST
        /public/v1/schema/alterSchema，body ``{"projectId", "schemaDraft":
        {"alterSpgTypes": [<spgType>]}}``。元素形态由
        ``read_type_to_draft`` 从 queryProjectSchema 读模型转换而来（读模型
        富化字段会 400——Spring 反序列化失败）。
        """
        if not isinstance(schema_draft, list) or not schema_draft:
            raise OpenSPGError("schema_draft 必须为非空 SPG type 数组")
        return await self._request(
            "POST",
            "/public/v1/schema/alterSchema",
            json_body={
                "projectId": int(project_id),
                "schemaDraft": {"alterSpgTypes": schema_draft},
            },
            # alter 触发服务端校验 + Neo4j 元数据同步，慢于查询
            timeout=60.0,
        )

    # —— 图概览（GraphController；无子图查询端点，M2 仅 allLabels，M0 侦察修正）——

    async def all_labels(self, project_id: str | int) -> Any:
        return await self._request(
            "GET", "/public/v1/graph/allLabels", params={"projectId": int(project_id)}
        )

    async def reason_run(
        self,
        project_id: str | int,
        dsl: str,
        params: dict[str, str] | None = None,
        *,
        timeout: float = 60.0,
    ) -> dict[str, Any]:
        """DSL 图查询（M3.4，经 reason/run——graph 控制器无子图查询端点）。

        M3.0/M3.3 实测契约：节点类型须 namespace 全名、关系 label 裸名；
        结果在 ``task.resultTableResult.rows``（``resultNodes``/``resultEdges``
        恒空）；错误详情在 ``task.resultMessage``。同步执行，超时较长。
        """
        result = await self._request(
            "POST",
            "/public/v1/reason/run",
            json_body={
                "projectId": int(project_id),
                "dsl": dsl,
                "params": params or {},
            },
            timeout=timeout,
        )
        return result if isinstance(result, dict) else {}
