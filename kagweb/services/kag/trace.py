# -*- coding: utf-8 -*-
"""kag_solve 轨迹元数据：把 Bridge JSON 结果归一为会话活动面板的图/引用契约。

M3.2 设计（docs/kag-integration-design.md §9 / 附录 A.2）：Bridge 的
``kag_solve`` tool_result 内容是 JSON 字符串
``{"answer", "reference", "subgraph", "cost_ms", "namespace"}``；而前端的会话
活动折叠（``web/lib/session-activity.ts``）从 tool_result 事件的
``metadata.tool_metadata.graph`` / ``metadata.sources`` / ``metadata.query``
读取 GraphRAG 同款子图与来源（dock 的 Graph/Files 签 +
``GraphSubgraphView`` cytoscape 画布）。本模块在 KAGWeb 侧完成形态转换：

- ``subgraph``（M3.0 实测形态：图数组，每图
  ``{class_name, result_nodes, result_edges}``，边以 ``_from``/``to`` 引用节点
  id）→ ``{nodes: [{id, label, type, description, degree}], edges:
  [{source, target, description, weight}]}``；多图按节点 id/无向边键去重合并；
- ``reference[0].info`` → ``sources``（``collectSessionSources`` 读取
  title/url 语义）；
- 工具入参 ``question`` → ``query``（折叠层同时读 result 的
  ``metadata.query``）；
- ``answer`` → 观察摘录替代原始 JSON 文本（机械抽取，非 LLM 摘要）。

边界对齐前端 ``readGraphSubgraph``（≤60 节点/≤120 边）：本模块先裁剪，前端
对持久化旧事件二次兜底。节点（chunk 实体）恒在、边依赖 kg_cs 命中（§5.1
实装注意二第 4 条）——无边时返回 ``None`` 的图，dock 不出 Graph 签。
"""

from __future__ import annotations

import json
import re
from typing import Any

#: 产出可归一轨迹元数据的 KAG Bridge 工具名（M1 工具面，docs 附录 A.1）。
KAG_TRACE_TOOLS = frozenset({"kag_solve"})


def is_kag_trace_tool(tool_name: str) -> bool:
    """是否为可归一轨迹的 KAG 工具（兼容 MCP 命名空间前缀）。

    CLI agent 后端按 MCP 约定把工具名上报为 ``mcp__<server>__<tool>``（M3.2
    live E2E 实测 claude-code 上报 ``mcp__kag-bridge__kag_solve``），裸名精确
    匹配会漏掉真实链路，故同时接受 ``__<tool>`` 结尾的带前缀形式。
    """
    if tool_name in KAG_TRACE_TOOLS:
        return True
    return any(tool_name.endswith(f"__{tool}") for tool in KAG_TRACE_TOOLS)

#: 与前端 readGraphSubgraph 相同的渲染边界（持久化事件防膨胀）。
_MAX_NODES = 60
_MAX_EDGES = 120

#: 节点 description（chunk 摘要 content）截断，控制持久化事件体积。
_MAX_DESCRIPTION_CHARS = 160

#: sources 条数上限（引用去重交给前端折叠层，这里只防异常大 payload）。
_MAX_SOURCES = 40


def kag_trace_metadata(
    tool_name: str, args: dict[str, Any] | None, result_text: str
) -> dict[str, Any] | None:
    """把一次 kag_solve 调用折叠为活动面板轨迹元数据。

    Args:
        tool_name: tool_result 的工具名（``mcp__<server>__kag_solve`` 或裸
            ``kag_solve``，见 ``is_kag_trace_tool``）。
        args: 配对 tool_call 的入参（``question`` 在此）。
        result_text: Bridge 返回的 JSON 文本。

    Returns:
        可并入 tool_result 事件 metadata 的字典（``query`` / ``sources`` /
        ``tool_metadata`` / ``excerpt``，仅含有值键），无法解析或无图无引用时
        返回 ``None``（调用方零副作用）。
    """
    if not is_kag_trace_tool(tool_name):
        return None
    payload = _parse_result(result_text)
    if payload is None:
        return None

    out: dict[str, Any] = {}
    query = str((args or {}).get("question") or "").strip()
    if query:
        out["query"] = query

    sources = _normalize_sources(payload.get("reference"))
    if sources:
        out["sources"] = sources

    graph = _normalize_graph(payload.get("subgraph"))
    if graph:
        out["tool_metadata"] = {"provider": "kag", "graph": graph}

    # answer 常内嵌 KAG 的引用标记（live 实测 ``<reference id="chunk:0_1">
    # </reference>``）——摘录是给 UI 展示的纯文本，剥掉再存（评审 N-2）。
    answer = re.sub(r"</?reference[^>]*>", "", str(payload.get("answer") or "")).strip()
    if answer:
        out["excerpt"] = answer

    # 无任何可归一产物（空 reference + 空 subgraph + 无 answer）时不注入，
    # 保持事件与未接入 KAG 前完全一致。
    return out or None


def _parse_result(result_text: str) -> dict[str, Any] | None:
    text = (result_text or "").strip()
    if not text.startswith("{"):
        return None
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    # Claude Code 的 stream-json 会把 MCP 工具结果再包一层
    # ``{"result": "<原始文本>"}``（M3.2 live E2E 实测；Bridge 线上返回直接
    # JSON，M3.1 裸 MCP 客户端已验证）。Bridge 自身不产出单 ``result`` 键，
    # 故该形态只可能是外层包装——剥一层用内层 JSON 继续。
    if list(payload.keys()) == ["result"] and isinstance(payload.get("result"), str):
        inner = payload["result"].strip()
        if inner.startswith("{"):
            try:
                unwrapped = json.loads(inner)
            except (json.JSONDecodeError, ValueError):
                unwrapped = None
            if isinstance(unwrapped, dict):
                return unwrapped
    return payload


def _normalize_sources(reference: Any) -> list[dict[str, Any]]:
    """``reference``（RefDocSet 数组）→ 前端 sources 记录（title/url 语义）。"""
    sources: list[dict[str, Any]] = []
    if not isinstance(reference, list):
        return sources
    for refset in reference:
        if not isinstance(refset, dict):
            continue
        infos = refset.get("info")
        if not isinstance(infos, list):
            continue
        for info in infos:
            if not isinstance(info, dict):
                continue
            title = str(info.get("document_name") or info.get("name") or "").strip()
            url = str(info.get("url") or "").strip()
            if not title and not url:
                continue
            entry: dict[str, Any] = {"type": str(refset.get("type") or "chunk")}
            if title:
                entry["title"] = title
            if url:
                entry["url"] = url
            sources.append(entry)
            if len(sources) >= _MAX_SOURCES:
                return sources
    return sources


def _normalize_graph(subgraph: Any) -> dict[str, Any] | None:
    """subgraph 图数组 → 前端 ToolGraphSubgraph（多图去重合并，有界）。"""
    if not isinstance(subgraph, list) or not subgraph:
        return None

    nodes: dict[str, dict[str, Any]] = {}
    edge_keys: set[str] = set()
    edges: list[dict[str, Any]] = []

    graphs = [graph for graph in subgraph if isinstance(graph, dict)]

    # 两遍法（评审 M-1）：先收编全部图的节点，再收边。逐图交替收集时，
    # 前一张图的边若引用后一张图才出现的节点，会被"孤立边"规则顺序敏感地
    # 误杀；先集齐节点后，边只受真实的存在性与上限约束。
    for graph in graphs:
        for raw in graph.get("result_nodes") or []:
            node = _normalize_node(raw)
            if node is None:
                continue
            existing = nodes.get(node["id"])
            # 同 id 节点：保留先到的，description 缺失时用后来的补齐。
            if existing is None:
                if len(nodes) >= _MAX_NODES:
                    continue
                nodes[node["id"]] = node
            elif not existing.get("description") and node.get("description"):
                existing["description"] = node["description"]

    for graph in graphs:
        for raw in graph.get("result_edges") or []:
            edge = _normalize_edge(raw)
            if edge is None:
                continue
            key = (
                f"{edge['source']}\x00{edge['target']}"
                if edge["source"] < edge["target"]
                else f"{edge['target']}\x00{edge['source']}"
            )
            if key in edge_keys or len(edges) >= _MAX_EDGES:
                continue
            # 两端都必须已收编（前端 readGraphSubgraph 同规则），孤立边丢弃。
            if edge["source"] not in nodes or edge["target"] not in nodes:
                continue
            edge_keys.add(key)
            edges.append(edge)

    if not nodes or not edges:
        return None

    degree: dict[str, int] = {node_id: 0 for node_id in nodes}
    for edge in edges:
        degree[edge["source"]] += 1
        degree[edge["target"]] += 1
    for node_id, node in nodes.items():
        node["degree"] = degree.get(node_id, 0)

    return {"nodes": list(nodes.values()), "edges": edges}


def _normalize_node(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    node_id = str(raw.get("id") or "").strip()
    if not node_id:
        return None
    # name 常带构建期的引号残留（M3.0 实测 "\"张三\""），剥掉再兜底 id。
    label = str(raw.get("name") or "").strip().strip('"“”') or node_id
    description = ""
    properties = raw.get("properties")
    if isinstance(properties, dict):
        description = str(properties.get("content") or "").strip()
    if len(description) > _MAX_DESCRIPTION_CHARS:
        description = description[:_MAX_DESCRIPTION_CHARS].rstrip() + "…"
    return {
        "id": node_id,
        "label": label,
        "type": str(raw.get("label") or "").strip(),
        "description": description,
        "degree": 0,
    }


def _normalize_edge(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    source = str(raw.get("_from") or raw.get("source") or "").strip()
    target = str(raw.get("to") or raw.get("target") or "").strip()
    if not source or not target or source == target:
        return None
    return {
        "source": source,
        "target": target,
        "description": str(raw.get("label") or "").strip(),
        "weight": 1,
    }
