# -*- coding: utf-8 -*-
"""M3.0① 收口探针：entity_linking 召回链路的三项实测。

疑点（think_pipeline 检索恒空的根因链最后一环）：
  S1. schema_helper.get_label_within_prefix 对 planner 输出类型
      （中文"人物"/英文"Person"）分别返回什么 —— SchemaUtils 映射是否成立
  S2. search_vector(label="Entity") 兜底是否命中（seed 数据是否有 Entity 向量索引）
  S3. search_text(query_string=name) 最终兜底是否命中（无 label 约束全文检索）

用法（与 m3_0 系列探针相同）：
  cd /tmp && KAG_PROJECT_DIR=/tmp/m0_kag_project \
    /tmp/kag_m0_venv/bin/python /Users/simon/project/kagweb/scripts/kag_m3/m3_0_schema_probe.py
"""

from __future__ import annotations

import json
import os
from pathlib import Path

RESULTS = Path(__file__).parent / "results" / "m3_0"
KAG_PROJECT_DIR = os.environ.get("KAG_PROJECT_DIR", "/tmp/m0_kag_project")


def summarize_nodes(nodes: list) -> list:
    out = []
    for n in nodes or []:
        node = n.get("node", n) if isinstance(n, dict) else {}
        out.append(
            {
                "labels": node.get("__labels__"),
                "name": (node.get("name") or "")[:40],
                "score": round(n.get("score", 0), 4) if isinstance(n, dict) else None,
            }
        )
    return out


def main() -> None:
    os.chdir(KAG_PROJECT_DIR)
    from kag.common.conf import KAG_CONFIG, KAG_PROJECT_CONF
    from kag.common.config import LogicFormConfiguration
    from kag.interface.solver.model.schema_utils import SchemaUtils
    from knext.search.client import SearchClient

    cfg = KAG_CONFIG.all_config
    project_id = KAG_PROJECT_CONF.project_id
    host_addr = KAG_PROJECT_CONF.host_addr
    vectorize_model = __import__(
        "kag.interface", fromlist=["VectorizeModelABC"]
    ).VectorizeModelABC.from_config(dict(cfg["vectorize_model"]))

    # --- S1: SchemaUtils 类型名映射 ---
    schema_helper = SchemaUtils(
        LogicFormConfiguration(
            {"KAG_PROJECT_ID": project_id, "KAG_PROJECT_HOST_ADDR": host_addr}
        )
    )
    probes = ["Person", "人物", "Organization", "Geo", "m0ProbeLive.Person", "Entity"]
    s1 = {
        "prefix": schema_helper.prefix,
        "get_label_within_prefix": {
            p: schema_helper.get_label_within_prefix(p) for p in probes
        },
        "node_en_2_full_name": dict(schema_helper.node_en_2_full_name),
        "node_zh_2_full_name": dict(schema_helper.node_zh_2_full_name),
    }

    # --- S2/S3: 向量与全文检索兜底 ---
    sc = SearchClient(host_addr=host_addr, project_id=int(project_id))
    vec = vectorize_model.vectorize("张三")

    def sv(label: str, key: str = "name") -> list:
        try:
            return summarize_nodes(
                sc.search_vector(label=label, property_key=key, query_vector=vec, topk=5)
            )
        except Exception as e:  # noqa: BLE001
            return [{"error": f"{type(e).__name__}: {e}"}]

    s2 = {
        "vector_label_full": sv("m0ProbeLive.Person"),  # 正向对照
        "vector_label_short": sv("Person"),  # planner 英文类型直用（无前缀）
        "vector_label_entity_fallback": sv("Entity"),  # entity_linking 兜底
        "vector_label_entity_desc": sv("Entity", "desc"),  # 内容向量兜底
    }

    try:
        st = summarize_nodes(sc.search_text(query_string="张三", topk=5))
    except Exception as e:  # noqa: BLE001
        st = [{"error": f"{type(e).__name__}: {e}"}]
    s3 = {"search_text_no_constraints": st}

    RESULTS.mkdir(parents=True, exist_ok=True)
    payload = {"s1_schema_mapping": s1, "s2_vector_fallbacks": s2, "s3_text_fallback": s3}
    out = RESULTS / "schema_probe.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
