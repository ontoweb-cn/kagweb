# -*- coding: utf-8 -*-
"""M0-3：knext 客户端直连 OpenSPG server 的契约探测。

验证目标（docs/kag-integration-design.md §10 第 3 项）：
以 KAG 官方客户端（knext，/public/v1/* 的权威消费方）逐端点记录请求/响应形状，
作为 KAGWeb 管理面自研 REST 客户端的契约基线。

覆盖端点（经 knext 间接验证）：
  - /public/v1/project  列表 + 查询 + 项目 config（knext/project/client.py）
  - /public/v1/schema   queryProjectSchema（ReasonerClient 初始化时经 SchemaSession 加载）
  - /public/v1/reason   reason_run_post（ReasonerClient.syn_execute，可选 --dsl）

前置条件：
  - 已安装 openspg-kag==0.8.0；OpenSPG server 可达（KAG_PROJECT_HOST_ADDR）。

用法：
  KAG_PROJECT_HOST_ADDR=http://127.0.0.1:8887 KAG_PROJECT_ID=1 \
    python m0_3_spg_knext_probe.py [--dsl "MATCH..."]
"""

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import write_result, print_step


def _obj_fields(obj) -> dict:
    """把 rest model 对象的字段摘成可读样本（供契约笔记）。"""
    return {key: _short(value) for key, value in obj.to_dict().items()}


def _short(value):
    text = str(value)
    return text if len(text) <= 200 else text[:200] + "…"


def run(args) -> dict:
    host = os.environ.get("KAG_PROJECT_HOST_ADDR", "")
    project_id = os.environ.get("KAG_PROJECT_ID", "")
    if not host:
        raise SystemExit("请设置 KAG_PROJECT_HOST_ADDR（OpenSPG server 地址）")

    steps = []

    # —— 1. 项目列表：GET /public/v1/project（knext ProjectApi.project_get）——
    from knext.project.client import ProjectClient

    pc = None  # 提前声明：步骤 1 失败时步骤 2 仍可优雅降级
    try:
        pc = ProjectClient(host_addr=host, project_id=int(project_id) if project_id else None)
        projects = pc._rest_client.project_get()
        sample = projects[0] if projects else None
        steps.append(
            {
                "step": "project_get（GET /public/v1/project）",
                "ok": True,
                "count": len(projects),
                "first_project_fields": _obj_fields(sample) if sample else None,
            }
        )
        print_step(True, "project_get", f"{len(projects)} 个项目")
    except Exception as exc:
        steps.append({"step": "project_get", "ok": False, "error": repr(exc)})
        print_step(False, "project_get", repr(exc))

    # —— 2. 项目 config：ProjectClient.get_config（Bridge 启动 KAG 推理同款路径）——
    if pc is None:
        steps.append({"step": "get_config", "ok": False, "error": "skipped: ProjectClient 未初始化"})
        print_step(False, "get_config", "跳过：步骤 1 失败")
    else:
        try:
            config = pc.get_config(project_id)
            steps.append(
                {
                    "step": "get_config（项目 config 字段解析）",
                    "ok": True,
                    "config_keys": sorted(config.keys()) if isinstance(config, dict) else _short(config),
                }
            )
            print_step(True, "get_config", f"keys={sorted(config.keys()) if isinstance(config, dict) else '非 dict'}")
        except Exception as exc:
            steps.append({"step": "get_config", "ok": False, "error": repr(exc)})
            print_step(False, "get_config", repr(exc))

    # —— 3. Schema：ReasonerClient 初始化即经 SchemaSession 调
    #        schema_query_project_schema_get（GET/POST /public/v1/schema 契约）——
    reasoner = None
    try:
        from knext.reasoner.client import ReasonerClient

        reasoner = ReasonerClient(host_addr=host, project_id=int(project_id))
        schema = reasoner.get_reason_schema()
        type_names = sorted(schema.keys())
        steps.append(
            {
                "step": "SchemaSession 加载（/public/v1/schema 契约）",
                "ok": True,
                "spg_type_count": len(type_names),
                "spg_types": type_names,
            }
        )
        print_step(True, "schema 加载", f"{len(type_names)} 个 SPG type")
    except Exception as exc:
        steps.append({"step": "SchemaSession 加载", "ok": False, "error": repr(exc)})
        print_step(False, "schema 加载", repr(exc))

    # —— 4. DSL 推理（可选）：reason_run_post（/public/v1/reason 契约）——
    if args.dsl and reasoner is not None:
        try:
            resp = reasoner.syn_execute(args.dsl)
            task = getattr(resp, "task", None)
            steps.append(
                {
                    "step": "syn_execute（reason_run_post）",
                    "ok": True,
                    "task_fields": _obj_fields(task) if task else None,
                }
            )
            print_step(True, "syn_execute", "DSL 执行成功")
        except Exception as exc:
            steps.append({"step": "syn_execute", "ok": False, "error": repr(exc)})
            print_step(False, "syn_execute", repr(exc))

    # —— 契约笔记：knext 调用的资源路径（供 m0_4 裸探测端点表核对）——
    # 注：rest api 对象不直接暴露 path，路径权威值从
    # knext/{project,reasoner,schema}/rest/*_api.py 的 resource_path 读取；
    # 这里记录方法名供人工对照。
    steps.append(
        {
            "step": "REST 方法对照（人工核对用）",
            "methods": [
                "ProjectApi.project_get / project_create_post / project_update_post",
                "ReasonerApi.reason_run_post / reasoner_dialog_report_completions_post",
                "SchemaSession → schema_query_project_schema_get",
            ],
            "note": "各方法 resource_path 见 knext/*/rest/*_api.py；m0_4 裸探测端点表以此为权威。",
        }
    )

    return {
        "host": host,
        "project_id": project_id,
        "steps": steps,
        "ok": all(s.get("ok", True) for s in steps if "ok" in s),
    }


def main():
    ap = argparse.ArgumentParser(description="M0-3 knext 契约探测")
    ap.add_argument("--dsl", default="", help="可选：测试 DSL（会真实执行推理，注意副作用）")
    args = ap.parse_args()

    result = {"ok": False}
    try:
        result = run(args)
    except Exception as exc:
        import traceback

        traceback.print_exc()
        result["error"] = repr(exc)
    write_result("m0_3_spg_knext_probe", result)


if __name__ == "__main__":
    main()
