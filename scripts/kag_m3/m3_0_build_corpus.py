# -*- coding: utf-8 -*-
"""M3.0① 语料构建：schema 约束抽取 + 向量化 + 写图（KAG 正式 builder 流程）。

背景：图写入端点直接 upsert 的节点无向量（entity linking 的 search_api 向量
检索召回不到），M0-1/M3.0 首轮 kag_solve 检索恒空。本脚本用
DefaultUnstructuredBuilderChain（DictReader >> SchemaConstraintExtractor >>
BatchVectorizer >> KGWriter）构建带向量的真实语料，验证 subgraph 轨迹链路。

用法（kag 环境，cwd 任意——chain 组件自 KAG_CONFIG 装配）：
  cd /tmp/m0_kag_project && /tmp/kag_m0_venv/bin/python scripts/kag_m3/m3_0_build_corpus.py
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

RESULTS = Path(__file__).parent / "results" / "m3_0"

# 与 m3_0_seed_graph 同域的小语料：人物-组织-地点关系（schema 已含
# Person/Organization/GeographicLocation + workFor/locateAt）
CORPUS = [
    {
        "id": "doc1",
        "name": "开元大学简介",
        "content": "张三是开元大学的教授，研究方向为知识图谱。开元大学位于杭州，"
        "是一所以人工智能见长的研究型大学。张三负责知识图谱实验室的日常工作。",
    },
    {
        "id": "doc2",
        "name": "智谱科技简介",
        "content": "李四是智谱科技的算法工程师。智谱科技位于北京，专注于大模型研发。"
        "李四负责推理框架的优化工作。",
    },
    {
        "id": "doc3",
        "name": "王五简介",
        "content": "王五也是开元大学的教师，与张三是同事。王五主讲数据库课程，"
        "研究方向为图数据库。",
    },
]


def main() -> None:
    os.chdir("/tmp/m0_kag_project")
    from kag.builder.component import KGWriter
    from kag.builder.component.reader.dict_reader import DictReader
    from kag.builder.component.extractor.schema_constraint_extractor import (
        SchemaConstraintExtractor,
    )
    from kag.builder.component.splitter.length_splitter import LengthSplitter
    from kag.builder.component.vectorizer.batch_vectorizer import BatchVectorizer
    from kag.builder.default_chain import DefaultUnstructuredBuilderChain

    from kag.common.conf import KAG_CONFIG
    from kag.interface import LLMClient, VectorizeModelABC

    llm = LLMClient.from_config(dict(KAG_CONFIG.all_config["llm"]))
    vectorize_model = VectorizeModelABC.from_config(
        dict(KAG_CONFIG.all_config["vectorize_model"])
    )
    chain = DefaultUnstructuredBuilderChain(
        reader=DictReader(id_col="id", name_col="name", content_col="content"),
        splitter=LengthSplitter(split_length=1000),
        extractor=SchemaConstraintExtractor(llm=llm),
        vectorizer=BatchVectorizer(vectorize_model=vectorize_model),
        writer=KGWriter(),
    )

    wrote = []
    for doc in CORPUS:
        # BuilderChainABC.invoke(file_path)：首节点输入 = [file_path]，
        # DictReader 直接收单个 dict（非列表）；chain 会就地消费 dict，传副本
        out = chain.invoke(dict(doc), max_workers=2)
        wrote.append({"doc": doc.get("id", "?"), "output_len": len(out)})

    RESULTS.mkdir(parents=True, exist_ok=True)
    out_path = RESULTS / "build_corpus.json"
    out_path.write_text(
        json.dumps({"wrote": wrote, "ok": True}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    print("builder done ->", wrote, "->", out_path)


if __name__ == "__main__":
    sys.exit(main())
