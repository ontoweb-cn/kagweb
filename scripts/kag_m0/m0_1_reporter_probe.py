# -*- coding: utf-8 -*-
"""M0-1 / M0-2：Reporter 事件探针 + qa()/do_qa_pipeline 注入差异验证。

验证目标（docs/kag-integration-design.md §10 第 1、2 项）：
  1. 继承 OpenSPGReporter 的探针能否收集推理全程事件（segment/tag/status 分布），
     并验证 generate_report_data() 的产物组装（StreamData: answer/think/reference/subgraph/metrics）；
  2. 确认 Bridge 应走 do_qa_pipeline()（reporter 显式传参）而非 qa()
     （后者在 kag/solver/main_solver.py L296-307 内部经 ReporterABC.from_config 构造
     OpenSPGReporter 并向 server 推流，无法注入自有 reporter）。

实现要点（证据：kag/solver/reporter/open_spg_reporter.py）：
  - OpenSPGReporter(task_id, host_addr=None, ...) 传 host_addr=None 时 self.client=None，
    do_report() 直接 return（L533-534）——父类纯内存，零网络；
  - add_report_line()（L472-530）只更新内存结构 report_stream_data / report_record /
    report_segment_time / report_sub_segment，无网络调用；
  - 探针覆盖 add_report_line 记录原始调用，覆盖 do_report 周期性快照
    generate_report_data() 的产物（模拟 Bridge 的 progress 帧）。

前置条件：
  - 已安装 openspg-kag==0.8.0（基线）且能 import kag/knext；
  - 在一个 KAG 项目目录内运行（含 kag_config.yaml，或用 --project-dir 指定），
    项目关联的 OpenSPG server 可达（solver 推理要拉 schema/图）；
  - kag_config.yaml 里的 llm 配置可用（推理要调 LLM）。

用法：
  cd <KAG 项目目录>
  KAG_PROJECT_HOST_ADDR=http://127.0.0.1:8887 KAG_PROJECT_ID=1 \
    python /path/to/m0_1_reporter_probe.py "张学友有什么歌" \
    --project-dir . --pipeline think_pipeline
"""

import argparse
import asyncio
import os
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import write_result, print_step, elapsed_ms


def build_probe_reporter(task_id: str, project_id, thinking_enabled: bool):
    """构造继承 OpenSPGReporter 的探针实例（设计 §5.1 的继承路线）。"""
    from kag.solver.reporter.open_spg_reporter import OpenSPGReporter

    class Probe(OpenSPGReporter):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            self.probe_events = []          # 原始 add_report_line 调用序列
            self.probe_snapshots = []       # 周期性产物快照（do_report 时机）
            self.probe_report_calls = 0

        def add_report_line(self, segment, tag_name, content, status, **kwargs):
            self.probe_events.append(
                {
                    "ts": round(time.time(), 3),
                    "segment": str(segment),
                    "tag": str(tag_name),
                    "status": str(status),
                    "content_len": len(str(content)),
                    "kwargs_keys": sorted(kwargs.keys()),
                }
            )
            super().add_report_line(segment, tag_name, content, status, **kwargs)

        def do_report(self):
            # host_addr=None → 父类此处本就 return；这里额外快照产物组装，
            # 验证 StreamData(answer/reference/think/subgraph/metrics) 可在 Bridge
            # 里按 progress 帧增量产出。
            self.probe_report_calls += 1
            try:
                content, status, _metrics = self.generate_report_data()
                self.probe_snapshots.append(
                    {
                        "ts": round(time.time(), 3),
                        "status": str(status),
                        "stream_data": content.to_dict(),
                    }
                )
            except Exception as exc:  # 早期空数据时组装可能异常，记录即可
                self.probe_snapshots.append(
                    {"ts": round(time.time(), 3), "error": repr(exc)}
                )

    # host_addr 显式 None：禁止向 server 推流（见文件头"实现要点"）
    return Probe(
        task_id=task_id,
        host_addr=None,
        project_id=project_id,
        thinking_enabled=thinking_enabled,
    )


async def run(args) -> dict:
    from kag.common.conf import KAGConfigAccessor

    try:
        # qa() 同款全局配置对象（kag/solver/main_solver.py L224/L293-295）
        from kag.common.conf import KAG_CONFIG, KAG_PROJECT_CONF
    except ImportError:  # 版本变动兜底：KAG 0.8.0 为准，失败仅提示
        KAG_CONFIG = KAG_PROJECT_CONF = None
        print_step(False, "导入 KAG_PROJECT_CONF/KAG_CONFIG", "版本差异，跳过全局配置更新")

    main_config = KAGConfigAccessor.get_config().all_config
    if not main_config:
        raise RuntimeError(
            "KAG 配置为空：请在 KAG 项目目录内运行（含 kag_config.yaml），或用 --project-dir 指定"
        )

    # —— 配置来源说明（M0 实测修正）——
    # kag_config.yaml 为唯一配置源：project.host_addr / llm / vectorizer 均从 yaml 加载。
    # 刻意不做 qa() 的运行时覆盖（KAG_PROJECT_CONF.host_addr = env）：该赋值会触发
    # KAG 配置内部重建并丢失 all_config 的 llm 键（repro3 实测，KeyError→占位符
    # 解析失败）；且 do_cycle_report 在 while 内吞 CancelledError，异常会被
    # asyncio 清理死锁掩盖（KAG bug，两个发现均已归档）。
    # TODO(M0-2)：qa() 的 llm extra_body 归一化与 KB 配置装配（L233-295）在
    # Bridge 实装时补齐；探针按单项目跑通。

    task_id = f"m0_probe_{int(time.time())}"
    reporter = build_probe_reporter(
        task_id=task_id,
        project_id=os.environ.get("KAG_PROJECT_ID"),
        thinking_enabled=args.pipeline in ("think_pipeline", "index_pipeline"),
    )

    from kag.solver.main_solver import do_qa_pipeline

    t0 = time.time()
    await reporter.start()
    try:
        answer = await do_qa_pipeline(
            args.pipeline,
            args.query,
            main_config,
            reporter,
            task_id=task_id,
            kb_project_ids=[],
        )
    finally:
        # 异常路径也必须 stop（置 _running=False），否则 KAG 的
        # do_cycle_report 吞 CancelledError 会让 asyncio.run 清理阶段死锁
        await reporter.stop()
    cost = elapsed_ms(t0)

    # —— 统计 ——
    events = reporter.probe_events
    seg_counter = Counter(e["segment"] for e in events)
    tag_counter = Counter(e["tag"] for e in events)
    status_counter = Counter(e["status"] for e in events)
    ok_snapshot = [s for s in reporter.probe_snapshots if "stream_data" in s]

    print_step(bool(events), "采集到 add_report_line 事件", f"共 {len(events)} 条")
    print_step(bool(ok_snapshot), "generate_report_data() 产物快照", f"共 {len(reporter.probe_snapshots)} 次 do_report")
    print_step(answer is not None and answer != "", "do_qa_pipeline 返回 answer", f"耗时 {cost}ms")

    return {
        "query": args.query,
        "pipeline": args.pipeline,
        "answer": str(answer),
        "cost_ms": cost,
        "event_count": len(events),
        "segment_distribution": dict(seg_counter),
        "top_tags": dict(tag_counter.most_common(30)),
        "status_distribution": dict(status_counter),
        "do_report_calls": reporter.probe_report_calls,
        "first_events": events[:20],
        "last_events": events[-10:],
        "snapshots": ok_snapshot[-3:],  # 终态附近快照（含 subgraph/reference 组装证据）
    }


def main():
    ap = argparse.ArgumentParser(description="M0-1/M0-2 Reporter 事件探针")
    ap.add_argument("query", help="测试问题")
    ap.add_argument("--project-dir", default=".", help="KAG 项目目录（含 kag_config.yaml）")
    ap.add_argument("--pipeline", default="think_pipeline", help="use_pipeline（think_pipeline/default_pipeline/...）")
    args = ap.parse_args()

    project_dir = Path(args.project_dir).resolve()
    os.chdir(project_dir)  # KAGConfigAccessor 依赖 cwd 下的 kag_config.yaml

    result = {"ok": False}
    try:
        result = asyncio.run(run(args))
        result["ok"] = result["event_count"] > 0 and bool(result["answer"])
    except Exception as exc:
        import traceback

        traceback.print_exc()
        result["error"] = repr(exc)
    write_result("m0_1_reporter_probe", result)


if __name__ == "__main__":
    main()
