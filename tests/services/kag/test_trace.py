# -*- coding: utf-8 -*-
"""M3.2 kag_solve 轨迹元数据归一（services/kag/trace.py）的契约测试。

fixture 形态取自 M3.0 实测归档（scripts/kag_m3/results/m3_0/
trajectory_probe.json 的 subgraph/reference 实测样本）：
图数组 {class_name, result_nodes, result_edges}，边以 _from/to 引用节点 id，
节点 name 带构建期引号残留。
"""

import json

from kagweb.services.kag.trace import KAG_TRACE_TOOLS, kag_trace_metadata

#: M3.0 实测样本裁剪版：两图（多轮 report round 重复），节点含 chunk 实体
#: 引号残留，reference 为 RefDocSet 数组。
BRIDGE_RESULT = json.dumps(
    {
        "answer": "根据开元大学简介中的信息，张三是开元大学的教授，因此张三任职于开元大学。",
        "reference": [
            {
                "id": "reference_ref_format",
                "type": "chunk",
                "info": [
                    {
                        "id": "chunk:0_1",
                        "content": "开元大学简介\n张三是开元大学的教授……",
                        "document_id": "doc1",
                        "document_name": "开元大学简介",
                        "url": None,
                    }
                ],
            }
        ],
        "subgraph": [
            {
                "class_name": "graph_genABC",
                "result_nodes": [
                    {
                        "id": '人物["张三"]',
                        "label": "人物",
                        "name": '"张三"',
                        "properties": {"name": '"张三"', "id": "张三"},
                    },
                    {
                        "id": '组织机构["开元大学"]',
                        "label": "组织机构",
                        "name": '"开元大学"',
                        "properties": {
                            "name": '"开元大学"',
                            "id": "开元大学",
                            "content": "开元大学简介 张三是开元大学的教授，研究方向为知识图谱。"
                            * 10,
                        },
                    },
                ],
                "result_edges": [
                    {
                        "id": '人物["张三"] 任职于 组织机构["开元大学"]',
                        "_from": '人物["张三"]',
                        "from_type": "人物",
                        "to": '组织机构["开元大学"]',
                        "to_type": "组织机构",
                        "label": "任职于",
                        "properties": {},
                    }
                ],
            },
            # 第二轮 report 的重复图：同节点同边——归一后必须去重。
            {
                "class_name": "graph_genXYZ",
                "result_nodes": [
                    {
                        "id": '人物["张三"]',
                        "label": "人物",
                        "name": '"张三"',
                        "properties": {"name": '"张三"', "id": "张三"},
                    }
                ],
                "result_edges": [
                    {
                        "_from": '人物["张三"]',
                        "to": '组织机构["开元大学"]',
                        "label": "任职于",
                    }
                ],
            },
        ],
        "cost_ms": 4057,
        "namespace": "m0ProbeLive",
    },
    ensure_ascii=False,
)


def test_kag_tools_set() -> None:
    assert "kag_solve" in KAG_TRACE_TOOLS
    assert "kag_schema" not in KAG_TRACE_TOOLS


def test_metadata_normalizes_graph_sources_query_and_excerpt() -> None:
    meta = kag_trace_metadata(
        "kag_solve", {"question": "张三任职于哪个组织机构？"}, BRIDGE_RESULT
    )
    assert meta is not None

    assert meta["query"] == "张三任职于哪个组织机构？"
    assert meta["excerpt"].startswith("根据开元大学简介")

    sources = meta["sources"]
    assert sources == [{"type": "chunk", "title": "开元大学简介"}]

    tool_metadata = meta["tool_metadata"]
    assert tool_metadata["provider"] == "kag"
    graph = tool_metadata["graph"]

    # 多图去重：两个节点、一条边（第二轮重复图不产生新条目）。
    assert len(graph["nodes"]) == 2
    assert len(graph["edges"]) == 1
    assert graph["edges"][0] == {
        "source": '人物["张三"]',
        "target": '组织机构["开元大学"]',
        "description": "任职于",
        "weight": 1,
    }

    by_id = {n["id"]: n for n in graph["nodes"]}
    person = by_id['人物["张三"]']
    # name 的构建期引号残留被剥掉。
    assert person["label"] == "张三"
    assert person["type"] == "人物"
    assert person["degree"] == 1
    org = by_id['组织机构["开元大学"]']
    # description 超长截断（_MAX_DESCRIPTION_CHARS=160 + 省略号）。
    assert len(org["description"]) <= 161
    assert org["description"].endswith("…")


def test_non_kag_tool_or_unparseable_returns_none() -> None:
    assert kag_trace_metadata("web_search", {}, BRIDGE_RESULT) is None
    assert kag_trace_metadata("kag_solve", None, "not json") is None
    assert kag_trace_metadata("kag_solve", None, "") is None
    # 可解析但无任何可归一产物（错误响应 {"error": ...}）→ 不注入。
    assert kag_trace_metadata("kag_solve", None, '{"error": "x"}') is None


def test_mcp_namespaced_tool_name_is_recognized() -> None:
    """CLI agent 后端把 MCP 工具上报为 ``mcp__<server>__<tool>``（live E2E
    实测 claude-code 为 ``mcp__kag-bridge__kag_solve``），前缀形式必须与裸名
    同样触发归一；近义后缀（如 ``__kag_schema``）仍不匹配。"""
    meta = kag_trace_metadata(
        "mcp__kag-bridge__kag_solve",
        {"question": "张三任职于哪个组织机构？"},
        BRIDGE_RESULT,
    )
    assert meta is not None
    assert meta["query"] == "张三任职于哪个组织机构？"
    assert meta["tool_metadata"]["provider"] == "kag"
    assert kag_trace_metadata("mcp__kag-bridge__kag_schema", {}, BRIDGE_RESULT) is None
    assert kag_trace_metadata("kag_solve__x", {}, BRIDGE_RESULT) is None


def test_claude_code_result_wrapper_is_unwrapped() -> None:
    """claude-code 的 stream-json 把 MCP 工具结果包成
    ``{"result": "<原始 JSON 文本>"}``（live E2E 实测）——剥层后归一产物与
    直接形态完全一致。"""
    wrapped = json.dumps({"result": BRIDGE_RESULT}, ensure_ascii=False)
    meta = kag_trace_metadata("mcp__kag-bridge__kag_solve", None, wrapped)
    direct = kag_trace_metadata("kag_solve", None, BRIDGE_RESULT)
    assert meta == direct
    assert meta is not None
    assert meta["tool_metadata"]["graph"]["nodes"]
    # 包装键但内层不是 JSON 对象（如 {"result": "pong"}）→ 不注入。
    assert kag_trace_metadata("kag_solve", None, '{"result": "pong"}') is None


def test_cross_graph_forward_edge_is_kept() -> None:
    """评审 M-1：前一张图的边引用后一张图才出现的节点（跨图共享节点），
    两遍法收集后必须保留，而非被"孤立边"规则顺序敏感地丢弃。"""
    payload = {
        "answer": "ok",
        "subgraph": [
            {
                "class_name": "graph_1",
                "result_nodes": [{"id": "a", "label": "A", "name": "A"}],
                # 引用 graph_2 才有的节点 b——旧逐图交替实现会丢这条边。
                "result_edges": [{"_from": "a", "to": "b", "label": "relates"}],
            },
            {
                "class_name": "graph_2",
                "result_nodes": [{"id": "b", "label": "B", "name": "B"}],
                "result_edges": [],
            },
        ],
    }
    meta = kag_trace_metadata("kag_solve", None, json.dumps(payload))
    graph = meta["tool_metadata"]["graph"]
    assert {n["id"] for n in graph["nodes"]} == {"a", "b"}
    assert graph["edges"] == [
        {"source": "a", "target": "b", "description": "relates", "weight": 1}
    ]


def test_excerpt_strips_reference_tags() -> None:
    """评审 N-2：answer 内嵌 KAG 引用标记（live 实测
    ``<reference id="chunk:0_1"></reference>``）——摘录是 UI 纯文本，剥掉。"""
    payload = {
        "answer": "张三是开元大学的教授<reference id=\"chunk:0_1\"></reference>。",
        "reference": [
            {"type": "chunk", "info": [{"document_name": "开元大学简介"}]}
        ],
    }
    meta = kag_trace_metadata("kag_solve", None, json.dumps(payload))
    assert meta["excerpt"] == "张三是开元大学的教授。"
    assert "<reference" not in meta["excerpt"]


def test_edgeless_graph_yields_no_graph_but_keeps_sources() -> None:
    """kg_cs 未命中时 subgraph 无边（§5.1 实装注意二第 4 条）——不出图签，
    但 chunk 引用与摘录仍归一。"""
    payload = json.loads(BRIDGE_RESULT)
    payload["subgraph"] = [
        {
            "class_name": "g",
            "result_nodes": [
                {"id": '人物["张三"]', "label": "人物", "name": '"张三"'}
            ],
            "result_edges": [],
        }
    ]
    meta = kag_trace_metadata("kag_solve", None, json.dumps(payload, ensure_ascii=False))
    assert meta is not None
    assert "tool_metadata" not in meta
    assert meta["sources"]
    assert meta["excerpt"]


def test_bounds_mirror_frontend_read_graph_subgraph() -> None:
    payload = json.loads(BRIDGE_RESULT)
    many_nodes = [
        {"id": f"n{i}", "label": "T", "name": f"node{i}"} for i in range(200)
    ]
    many_edges = [
        {"_from": f"n{i}", "to": f"n{i + 1}", "label": "p"} for i in range(199)
    ]
    payload["subgraph"] = [
        {"class_name": "big", "result_nodes": many_nodes, "result_edges": many_edges}
    ]
    meta = kag_trace_metadata("kag_solve", None, json.dumps(payload))
    graph = meta["tool_metadata"]["graph"]
    assert len(graph["nodes"]) == 60
    # 邻接链 n_i→n_{i+1}：仅两端都落在 60 节点上限内的边收编（n0..n58）。
    assert len(graph["edges"]) == 59
    # 孤立边（两端未收编）不产生条目。
    payload["subgraph"][0]["result_edges"] = [
        {"_from": "n0", "to": "missing", "label": "p"}
    ]
    meta = kag_trace_metadata("kag_solve", None, json.dumps(payload))
    assert "tool_metadata" not in meta
