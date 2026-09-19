# -*- coding: utf-8 -*-
"""M3.0① 轨迹数据形态探针：bridge 路径下 StreamData.subgraph 是否可用。

背景（docs/kag-integration-design.md §9 M3.2 前置）：M0-1 实测 subgraph 为
空数组，但事件流存在 generator_reference_graphs/reference_graph（有内容）。
OpenSPGReporter 源码链路：thinker 段 kg 检索事件的 content 为
RelationData/EntityData 列表时，generate_report_data → process_think →
generate_content → _convert_spo_to_graph 组装 SubGraph 进 StreamData。

本探针与 m0_1 的差异：问题针对图中已知实体（经 reason DSL 勘察选定），
验证 KG 检索命中场景下 subgraph 的真实形态（nodes/edges/properties 字段），
为 M3.2 轨迹可视化（cytoscape 数据模型）与 D4 决策提供依据。

用法（kag 环境，与 m0_1 相同）：
  cd /tmp && KAG_PROJECT_DIR=/tmp/m0_kag_project KAG_PROJECT_HOST_ADDR=http://127.0.0.1:8887 \
    /tmp/kag_m0_venv/bin/python /Users/simon/project/kagweb/scripts/kag_m3/m3_0_trajectory_probe.py "<问题>"
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

RESULTS = Path(__file__).parent / "results" / "m3_0"
KAG_PROJECT_DIR = os.environ.get("KAG_PROJECT_DIR", "/tmp/m0_kag_project")
QUESTION = sys.argv[1] if len(sys.argv) > 1 else "OpenSPG 是什么？"


def main() -> None:
    os.chdir(KAG_PROJECT_DIR)
    from kag.common.conf import KAGConfigAccessor

    cfg = KAGConfigAccessor.get_config().all_config
    from kag.solver.reporter.open_spg_reporter import OpenSPGReporter

    events: list[dict] = []
    object_events: list[dict] = []

    class ProbeReporter(OpenSPGReporter):
        """与 bridge._BridgeReporter 同构：事件桥接 + do_report 置空。

        add_report_line 保留父类行为（原始 RelationData/EntityData 对象存入
        report_stream_data——subgraph 组装依赖这一点）。
        """

        def add_report_line(self, segment, tag_name, content, status, **kwargs):
            super().add_report_line(segment, tag_name, content, status, **kwargs)
            content_kind = type(content).__name__ if not isinstance(content, str) else "str"
            events.append(
                {
                    "segment": segment,
                    "tag": tag_name,
                    "status": status,
                    "content_kind": content_kind,
                    "content_len": len(content) if hasattr(content, "__len__") else -1,
                }
            )
            # M3.0①：对象型 content（RetrieverOutput/KgGraph）的内部结构 dump
            # ——决定 bridge 侧 subgraph 提取的可行数据面
            if content_kind not in ("str", "list", "dict"):
                object_dump = None
                for attempt in ("to_dict", "to_show"):
                    if hasattr(content, attempt):
                        try:
                            object_dump = getattr(content, attempt)()
                            break
                        except Exception:  # noqa: BLE001
                            continue
                if object_dump is None:
                    object_dump = repr(content)
                object_events.append(
                    {
                        "segment": segment,
                        "tag": tag_name,
                        "kind": content_kind,
                        "dump": object_dump,
                    }
                )

        def do_report(self):
            pass  # bridge 同款：终态产物统一在结束时组装

    task_id = f"m3_0_{int(time.time() * 1000)}"
    project_id = str(cfg.get("project", {}).get("id", ""))
    namespace = str(cfg.get("project", {}).get("namespace", ""))
    reporter = ProbeReporter(task_id=task_id, host_addr=None, project_id=project_id)

    from kag.solver.main_solver import do_qa_pipeline

    t0 = time.time()
    answer = asyncio.run(
        do_qa_pipeline(
            "think_pipeline",
            QUESTION,
            cfg,
            reporter,
            task_id=task_id,
            kb_project_ids=[],
        )
    )
    asyncio.run(reporter.stop())
    cost_ms = int((time.time() - t0) * 1000)

    # bridge.kag_solve 同款终态组装
    stream_data = {}
    try:
        content, _status, _metrics = reporter.generate_report_data()
        stream_data = content.to_dict()
    except Exception as exc:  # noqa: BLE001
        print(f"WARN generate_report_data: {exc!r}", file=sys.stderr)

    subgraph = stream_data.get("subgraph") or []
    nodes_edges = []
    for graph in subgraph:
        if hasattr(graph, "to_dict"):
            graph = graph.to_dict()
        nodes_edges.append(
            {
                "class_name": graph.get("class_name"),
                "nodes": [
                    n.to_dict() if hasattr(n, "to_dict") else n
                    for n in (graph.get("result_nodes") or [])
                ],
                "edges": [
                    e.to_dict() if hasattr(e, "to_dict") else e
                    for e in (graph.get("result_edges") or [])
                ],
            }
        )

    RESULT = {
        "question": QUESTION,
        "project": {"id": project_id, "namespace": namespace},
        "cost_ms": cost_ms,
        "answer": str(answer)[:2000],
        "event_count": len(events),
        "events": events,
        "object_events": object_events,
        "stream_data_summary": {
            "answer_len": len(str(stream_data.get("answer", ""))),
            "think_len": len(str(stream_data.get("think", ""))),
            "reference_count": len(stream_data.get("reference") or []),
            "subgraph_count": len(subgraph),
        },
        "reference": stream_data.get("reference", []),
        "subgraph": nodes_edges,
        "ok": True,
    }
    RESULTS.mkdir(parents=True, exist_ok=True)
    out = RESULTS / "trajectory_probe.json"
    out.write_text(
        json.dumps(RESULT, ensure_ascii=False, indent=1, default=str), encoding="utf-8"
    )
    print(
        f"subgraph graphs={len(nodes_edges)} "
        f"nodes={sum(len(g['nodes']) for g in nodes_edges)} "
        f"edges={sum(len(g['edges']) for g in nodes_edges)} | "
        f"events={len(events)} | answer {cost_ms}ms -> {out}"
    )
    for e in events:
        if e["content_kind"] not in ("str",):
            print(f"  object-content event: {e['segment']}/{e['tag']} kind={e['content_kind']}")


if __name__ == "__main__":
    main()
