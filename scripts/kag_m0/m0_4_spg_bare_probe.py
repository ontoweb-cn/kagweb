# -*- coding: utf-8 -*-
"""M0-4：OpenSPG server 裸 HTTP 鉴权探测（仅标准库，无 kag 依赖）。

验证目标（docs/kag-integration-design.md §10 第 4 项）：
  1. 不带任何 token/header 直调 /public/v1/* 各端点，验证"server 无鉴权、
     信任调用方"的结论（GET 可达 + 写端点不被 401/403 拦截 = 证实；设计 §6.1 据此
     要求 server 仅内网部署）；
  2. 记录各端点真实路径/响应形状——子路径以 m0_3 输出与 knext/*/rest/*_api.py
     的 resource_path 为权威，本表候选路径探测结果一并记录。

安全约定：
  - 默认只跑无副作用的 GET；POST（创建项目/写图/提交构建）需显式 --allow-write，
    并统一带 m0-probe 命名空间标记，便于事后清理；
  - 本脚本绝不自动执行 --allow-write 之外的写操作。

用法：
  python m0_4_spg_bare_probe.py --host http://127.0.0.1:8887 [--allow-write]
"""

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import write_result, print_step

# 端点表（路径证据：openspg/server/api/http-server/.../openapi/*.java）。
# 子路径不确定处给出候选列表，探测时逐一尝试并记录命中的那个。
GET_PROBES = [
    ("GET", "/public/v1/project", None, "项目列表（ProjectController）"),
    ("GET", "/public/v1/tenant", None, "租户列表（TenantController）"),
    # schema 查询：knext SchemaSession.schema_query_project_schema_get 的
    # resource_path 见 knext/reasoner/rest（M0-3 契约笔记核对），候选：
    ("GET", "/public/v1/schema/queryProjectSchema?projectId={pid}", None, "项目 Schema（候选路径 A）"),
    ("GET", "/public/v1/schema?projectId={pid}", None, "项目 Schema（候选路径 B）"),
]

WRITE_PROBES = [
    (
        "POST",
        "/public/v1/project",
        {
            "name": "m0-probe-project",
            "namespace": "m0_probe_ns",
            "config": {},
            "visibility": "PRIVATE",
            "tag": "LOCAL",
            "userNo": "kagweb-m0-probe",  # 设计 §6.3 的归因格式预演
        },
        "创建项目（写；--allow-write 才执行）",
    ),
    (
        "POST",
        "/public/v1/builder/kag/submit",
        {"projectId": "{pid}", "workerNum": 1, "command": "echo m0-probe"},
        "提交 KAG_COMMAND 构建（写；--allow-write 才执行）",
    ),
]


def probe(host: str, method: str, path: str, body, desc: str) -> dict:
    url = host.rstrip("/") + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    # 注意：刻意不携带任何 Authorization —— 本探测的目的就是验证无鉴权可达性
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            status, payload = resp.status, resp.read(800).decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        status, payload = exc.code, exc.read(800).decode("utf-8", "replace")
    except Exception as exc:
        return {"method": method, "path": path, "desc": desc, "ok": False, "error": repr(exc)}

    # 鉴权判定：401/403 = 存在鉴权；200 或参数类 4xx/5xx = 端点无鉴权可达
    has_auth = status in (401, 403)
    return {
        "method": method,
        "path": path,
        "desc": desc,
        "ok": True,
        "status": status,
        "auth_required": has_auth,
        "body_head": payload,
    }


def main():
    ap = argparse.ArgumentParser(description="M0-4 裸 HTTP 鉴权探测")
    ap.add_argument("--host", required=True, help="OpenSPG server 地址，如 http://127.0.0.1:8887")
    ap.add_argument("--project-id", default="1", help="schema/reason 探测用的 projectId")
    ap.add_argument("--allow-write", action="store_true", help="显式开启写端点探测（有副作用）")
    args = ap.parse_args()

    results = []
    for method, path, body, desc in GET_PROBES:
        real_path = path.replace("{pid}", args.project_id)
        r = probe(args.host, method, real_path, body, desc)
        results.append(r)
        print_step(
            r.get("ok") and not r.get("auth_required"),
            f"{method} {real_path}",
            f"status={r.get('status')} auth={r.get('auth_required')} — {desc}",
        )

    if args.allow_write:
        for method, path, body, desc in WRITE_PROBES:
            real_body = (
                {k: v.replace("{pid}", args.project_id) if isinstance(v, str) else v for k, v in body.items()}
                if body
                else None
            )
            r = probe(args.host, method, path, real_body, desc)
            results.append(r)
            print_step(
                r.get("ok") and not r.get("auth_required"),
                f"{method} {path}",
                f"status={r.get('status')} auth={r.get('auth_required')} — {desc}",
            )
    else:
        print("\n[跳过] 写端点未执行（加 --allow-write 开启；会产生 m0_probe_ns 等测试数据）")

    reachable_without_auth = [r for r in results if r.get("ok") and not r.get("auth_required")]
    write_result(
        "m0_4_spg_bare_probe",
        {
            "host": args.host,
            "probes": results,
            "conclusion": {
                "probed": len(results),
                "reachable_without_auth": len(reachable_without_auth),
                "note": (
                    "若全部 reachable_without_auth=probed：证实 /public/v1 无鉴权、信任调用方，"
                    "KAGWeb 必须是唯一安全边界且 server 仅内网部署（设计 §6.1/§11-2）。"
                ),
            },
        },
    )


if __name__ == "__main__":
    main()
