# -*- coding: utf-8 -*-
"""M0-5：图存储后端确认。

验证目标（docs/kag-integration-design.md §10 第 5 项 + 评审 A1）：
确认 OpenSPG server 的图存储后端类型（TuGraph 或其他）与连通性。
solver 图检索直连图存储（KAG_GRAPH_STORE_URI，不经 /public/v1 REST），
图存储是独立部署依赖，其直连端口必须纳入内网隔离边界（§6.1）。

做法：
  1. 静态扫描（默认）：在 openspg server 源码中找图存储后端证据——
     pom 依赖 artifactId、源码关键词（tugraph/neo4j/janusgraph/tinkerpop/geabase）、
     配置项（graph.store 等）；
  2. 连通性探测（可选）：探测图存储端口 TCP 可达性
     （--uri 显式给定，或读环境变量 KAG_GRAPH_STORE_URI）。

用法：
  python m0_5_graph_store_check.py [--openspg-dir ~/project/openspgapp/openspg] [--uri ...]
"""

import argparse
import os
import re
import socket
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import write_result, print_step

# 后端关键词 → 归一化名称（pom 依赖与源码共用）
BACKEND_PATTERNS = [
    (r"(?i)tugraph", "TuGraph"),
    (r"(?i)geabase", "GeaBase"),
    (r"(?i)neo4j", "Neo4j"),
    (r"(?i)janusgraph", "JanusGraph"),
    (r"(?i)tinkerpop|gremlin", "TinkerPop/Gremlin"),
]

SCAN_EXTS = {".java", ".scala", ".kt", ".yml", ".yaml", ".properties", ".xml"}


def scan_backends(openspg_dir: Path) -> dict:
    """扫描源码/构建/配置文件中的图存储后端证据。"""
    hits = {name: [] for _, name in BACKEND_PATTERNS}
    config_keys = []
    scanned = 0
    for path in openspg_dir.rglob("*"):
        if path.suffix.lower() not in SCAN_EXTS or not path.is_file():
            continue
        scanned += 1
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        rel = str(path.relative_to(openspg_dir))
        for pat, name in BACKEND_PATTERNS:
            if re.search(pat, text):
                # pom 依赖命中权重最高：单独记录 artifactId 行
                for m in re.finditer(r"<(?:artifactId|groupId)>([^<]+)</", text):
                    if re.search(pat, m.group(1)):
                        hits[name].append(f"{rel} :: <dep> {m.group(1)}")
                # 源码/配置命中记前 3 处即可（防刷屏）
                for i, line in enumerate(text.splitlines(), 1):
                    if re.search(pat, line):
                        hits[name].append(f"{rel}:{i} :: {line.strip()[:120]}")
                        if sum(1 for h in hits[name] if f"{rel}:" in h or f"{rel} " in h) >= 3:
                            break
        # 图存储配置键（无论后端）
        for i, line in enumerate(text.splitlines(), 1):
            if re.search(r"(?i)graph[._-]?store", line) and "=" in line or ":" in line:
                config_keys.append(f"{rel}:{i} :: {line.strip()[:120]}")
    return {"scanned_files": scanned, "hits": {k: v[:8] for k, v in hits.items() if v}, "config_keys": config_keys[:10]}


def probe_uri(uri: str) -> dict:
    """探测图存储地址的 TCP 可达性（bolt://、http://、host:port 均可）。"""
    m = re.match(r"(?:\w+://)?([^:/?]+):(\d+)", uri)
    if not m:
        return {"uri": uri, "ok": False, "error": "无法解析 host:port"}
    host, port = m.group(1), int(m.group(2))
    try:
        with socket.create_connection((host, port), timeout=5):
            return {"uri": uri, "host": host, "port": port, "ok": True}
    except Exception as exc:
        return {"uri": uri, "host": host, "port": port, "ok": False, "error": repr(exc)}


def main():
    ap = argparse.ArgumentParser(description="M0-5 图存储后端确认")
    ap.add_argument("--openspg-dir", default=str(Path.home() / "project" / "openspgapp" / "openspg"), help="OpenSPG server 源码目录（仅静态扫描证据用）")
    ap.add_argument("--uri", default="", help="图存储地址（缺省读 KAG_GRAPH_STORE_URI；留空则跳过探测）")
    args = ap.parse_args()

    openspg_dir = Path(args.openspg_dir).expanduser().resolve()
    result = {"static": None, "connectivity": None}

    if openspg_dir.is_dir():
        result["static"] = scan_backends(openspg_dir)
        backends = list(result["static"]["hits"].keys())
        print_step(bool(backends), "图存储后端证据", f"命中 {backends}（扫描 {result['static']['scanned_files']} 个文件）")
    else:
        print_step(False, "静态扫描跳过", f"目录不存在：{openspg_dir}")

    uri = args.uri or os.environ.get("KAG_GRAPH_STORE_URI", "")
    if uri:
        result["connectivity"] = probe_uri(uri)
        ok = result["connectivity"].get("ok")
        print_step(bool(ok), "图存储连通性", f"{uri} -> {'可达' if ok else result['connectivity'].get('error')}")
    else:
        print_step(True, "连通性探测跳过", "未提供 --uri 且无 KAG_GRAPH_STORE_URI")

    result["note"] = (
        "最小部署（docker compose 单机）按 §10 第 5 项人工验证；"
        "图存储直连端口必须纳入隔离边界（§6.1，评审 A1）。"
    )
    write_result("m0_5_graph_store_check", result)


if __name__ == "__main__":
    main()
