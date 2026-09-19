# -*- coding: utf-8 -*-
"""M3.0 数据种子：为 m0ProbeLive 构建可触发 KG 检索的最小图谱。

三件事一并实测（M3.0 归档）：
1. alterSchema 契约（M3.5 前置）：SchemaClient.add_relation + commit，
   给 Person 加 workFor(→Organization) / locateAt(→GeographicLocation)；
2. 图写入契约：GraphApi.graph_upsert_vertex/edge_post（M3.4 前置）；
3. 写入后用 reason DSL 回查（M3.0②）。

用法（kag 环境）：
  /tmp/kag_m0_venv/bin/python scripts/kag_m3/m3_0_seed_graph.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HOST = "http://127.0.0.1:8887"
PROJECT_ID = 3
NS = "m0ProbeLive"
RESULTS = Path(__file__).parent / "results" / "m3_0"

RECORD: dict = {"steps": []}


def _log(step: str, ok: bool, detail: str = "") -> None:
    RECORD["steps"].append({"step": step, "ok": ok, "detail": detail[:400]})
    print(("PASS " if ok else "FAIL ") + step + (f" | {detail[:200]}" if detail else ""))


def seed_schema() -> None:
    from knext.schema.client import SchemaClient
    from knext.schema.model.relation import Relation

    # SchemaClient(host, id) 是 REST 门面；alter 走 create_session() 的
    # SchemaSession（get/add_relation/commit——knext/schema/client.py）。
    #
    # M3.0 修正（关键契约）：commit() 只提交 _alter_spg_types 列表里的类型；
    # session.get() 拿到的对象就地 add_relation 后必须 update_type() 登记，
    # 否则 alter 永远不会发往服务端。且 create_session() 按 project_id 进程内
    # 缓存——同进程回读是假阳性，验证必须在独立进程做（见 main 尾部 subprocess）。
    base = SchemaClient(host_addr=HOST, project_id=PROJECT_ID)
    session = base.create_session()
    person = session.get(f"{NS}.Person")
    before = list(person.relations.keys())
    if any(k.startswith("workFor") for k in before) and any(
        k.startswith("locateAt") for k in before
    ):
        _log("alterSchema: submit", True, "already present (idempotent skip)")
        return
    person.add_relation(
        Relation(name="workFor", object_type_name=f"{NS}.Organization",
                 name_zh="任职于", desc="person works for organization")
    )
    person.add_relation(
        Relation(name="locateAt", object_type_name=f"{NS}.GeographicLocation",
                 name_zh="位于", desc="subject located at place")
    )
    session.update_type(person)
    session.commit()
    _log("alterSchema: submit", True, f"before={before} (update_type + commit)")


def verify_schema_cross_process() -> None:
    """跨进程回读（绕开 create_session 的进程内缓存）——alterSchema 契约证据。"""
    import subprocess
    import sys

    code = (
        "from knext.schema.client import SchemaClient\n"
        f"sc = SchemaClient(host_addr='{HOST}', project_id={PROJECT_ID})\n"
        "s = sc.create_session()\n"
        "p = s.get(f'{%r}.Person')\n" % NS
        + "print('[' + ','.join(sorted(p.relations.keys())) + ']')\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=60
    )
    after = out.stdout.strip()
    _log(
        "alterSchema: cross-process verify",
        "workFor_" in after and "locateAt_" in after,
        f"relations={after}",
    )


def _prop(name: str, value: str):
    from knext.graph.rest.models.lpg_property_record import LpgPropertyRecord

    return LpgPropertyRecord(name=name, value=value)


def seed_graph() -> None:
    from knext.graph.rest.graph_api import GraphApi
    from knext.graph.rest.models.edge_record_instance import EdgeRecordInstance
    from knext.graph.rest.models.upsert_edge_request import UpsertEdgeRequest
    from knext.graph.rest.models.upsert_vertex_request import UpsertVertexRequest
    from knext.graph.rest.models.vertex_record_instance import VertexRecordInstance
    from knext.common.rest.api_client import ApiClient
    from knext.common.rest.configuration import Configuration

    api = GraphApi(api_client=ApiClient(configuration=Configuration(host=HOST)))

    # UpsertVertexRequest.vertices 是 VertexRecordInstance 列表（properties 为
    # 自由 dict——非管理面 VertexRecord 的 LpgPropertyRecord 形态；
    # vectors 模型校验要求非 None，空 dict 占位）。
    # 实测批语义：一个请求内全部 vertex 落成首元素的 type——按类型分组发。
    def vertex(vtype: str, vid: str, name: str):
        return VertexRecordInstance(
            type=f"{NS}.{vtype}", id=vid, properties={"name": name}, vectors={}
        )

    groups = [
        [vertex("Person", "ZhangSan", "张三"),
         vertex("Person", "LiSi", "李四"),
         vertex("Person", "WangWu", "王五")],
        [vertex("Organization", "KaiYuanUniv", "开元大学"),
         vertex("Organization", "ZhiPuTech", "智谱科技")],
        [vertex("GeographicLocation", "Hangzhou", "杭州"),
         vertex("GeographicLocation", "Beijing", "北京")],
    ]
    total = sum(len(g) for g in groups)
    try:
        for batch in groups:
            api.graph_upsert_vertex_post(
                upsert_vertex_request=UpsertVertexRequest(
                    project_id=PROJECT_ID, vertices=batch
                )
            )
        _log("graph upsert vertices", True, f"{total} nodes ({len(groups)} type groups)")
    except Exception as exc:  # noqa: BLE001
        _log("graph upsert vertices", False, repr(exc))

    def edge(src: str, src_type: str, label: str, dst: str, dst_type: str):
        return EdgeRecordInstance(
            src_type=f"{NS}.{src_type}",
            src_id=src,
            dst_type=f"{NS}.{dst_type}",
            dst_id=dst,
            label=label,
            properties={},
        )

    edges = [
        edge("ZhangSan", "Person", "workFor", "KaiYuanUniv", "Organization"),
        edge("LiSi", "Person", "workFor", "ZhiPuTech", "Organization"),
        edge("WangWu", "Person", "workFor", "KaiYuanUniv", "Organization"),
        edge("ZhangSan", "Person", "locateAt", "Hangzhou", "GeographicLocation"),
        edge("KaiYuanUniv", "Organization", "locateAt", "Hangzhou", "GeographicLocation"),
        edge("ZhiPuTech", "Organization", "locateAt", "Beijing", "GeographicLocation"),
    ]
    try:
        api.graph_upsert_edge_post(
            upsert_edge_request=UpsertEdgeRequest(
                project_id=PROJECT_ID, upsert_adjacent_vertices=False, edges=edges
            )
        )
        _log("graph upsert edges", True, f"{len(edges)} edges")
    except Exception as exc:  # noqa: BLE001
        _log("graph upsert edges", False, repr(exc))


def verify_dsl() -> None:
    import urllib.request

    def run(dsl: str):
        req = urllib.request.Request(
            f"{HOST}/public/v1/reason/run",
            data=json.dumps(
                {"projectId": PROJECT_ID, "dsl": dsl, "params": {}}
            ).encode(),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read())

    checks = {
        "person count": "MATCH (n:m0ProbeLive.Person) RETURN n.id, n.name",
        "workFor edges": "MATCH (n:m0ProbeLive.Person)-[p:workFor]->(o:m0ProbeLive.Organization) RETURN n.id, o.id",
    }
    for name, dsl in checks.items():
        try:
            task = run(dsl).get("task", {})
            rows = (task.get("resultTableResult") or {}).get("rows") or []
            _log(f"DSL {name}", task.get("status") == "FINISH" and len(rows) > 0,
                 f"rows={json.dumps(rows, ensure_ascii=False)[:300]}")
        except Exception as exc:  # noqa: BLE001
            _log(f"DSL {name}", False, repr(exc))


def cleanup_graph() -> None:
    """幂等：先清掉本探针写过的节点（首次批语义踩坑写错 label 的遗留）。"""
    from knext.graph.rest.graph_api import GraphApi
    from knext.graph.rest.models.delete_vertex_request import DeleteVertexRequest
    from knext.graph.rest.models.vertex_record_instance import VertexRecordInstance
    from knext.common.rest.api_client import ApiClient
    from knext.common.rest.configuration import Configuration

    api = GraphApi(api_client=ApiClient(configuration=Configuration(host=HOST)))
    stale = [
        "ZhangSan", "LiSi", "WangWu", "KaiYuanUniv", "ZhiPuTech",
        "Hangzhou", "Beijing", "ProbeOrg",
    ]
    for vtype in ("Person", "Organization", "GeographicLocation"):
        try:
            api.graph_delete_vertex_post(
                delete_vertex_request=DeleteVertexRequest(
                    project_id=PROJECT_ID,
                    vertices=[
                        VertexRecordInstance(
                            type=f"{NS}.{vtype}", id=vid, properties={}, vectors={}
                        )
                        for vid in stale
                    ],
                )
            )
        except Exception as exc:  # noqa: BLE001
            _log(f"cleanup {vtype}", False, repr(exc))
    _log("cleanup stale vertices", True, f"ids={stale}")


def main() -> None:
    cleanup_graph()
    seed_schema()
    seed_graph()
    verify_dsl()
    verify_schema_cross_process()
    RESULTS.mkdir(parents=True, exist_ok=True)
    out = RESULTS / "seed_graph.json"
    out.write_text(json.dumps(RECORD, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"-> {out}")


if __name__ == "__main__":
    sys.exit(main())
