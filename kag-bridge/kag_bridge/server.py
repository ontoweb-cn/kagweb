# -*- coding: utf-8 -*-
"""KAG Bridge — MCP Server（M1 MVP）。

设计文档：docs/kag-integration-design.md §5.1 + 附录 A。
单项目 MVP：Bridge 实例经 KAG_PROJECT_DIR 绑定一个 KAG 项目；显式传入
不同 project_id 返回结构化错误（多项目路由 M2 提供）。

内建的 M0 实测结论（results/m0_1，勿改）：
  1. 配置唯一来源 = KAG_PROJECT_DIR/kag_config.yaml；禁止运行时执行
     KAG_PROJECT_CONF.host_addr = <env>（会丢 all_config 的 llm 键）；
  2. reporter 生命周期必须 try/finally 包 reporter.stop()（do_cycle_report
     吞 CancelledError，异常路径不 stop 会死锁 asyncio 清理并掩盖 traceback）；
  3. OpenSPGReporter(host_addr=None) 纯内存零网络，add_report_line 无副作用。
"""

import asyncio
import json
import os
import sys
import threading
import time
import urllib.request
from pathlib import Path

from mcp.server.fastmcp import Context, FastMCP

KAG_PROJECT_DIR = os.environ.get("KAG_PROJECT_DIR", "").strip()

mcp = FastMCP("kag-bridge")

# —— KAG 延迟加载（MCP 服务可先起，首个工具调用时加载 KAG）——
_KAG_READY = False
_MAIN_CONFIG: dict = {}
_kag_init_lock = threading.Lock()

# M1 单并发：KAGConfigAccessor 是进程级全局状态，并发 solve 需排队（§5.1 R3 的最小实现）
_solve_semaphore = asyncio.Semaphore(1)


def _ensure_kag() -> None:
    global _KAG_READY, _MAIN_CONFIG
    if _KAG_READY:
        return
    with _kag_init_lock:
        if _KAG_READY:  # 双检：等锁期间可能已被并发首调完成
            return
        if not KAG_PROJECT_DIR or not Path(KAG_PROJECT_DIR, "kag_config.yaml").is_file():
            raise RuntimeError(
                "KAG_PROJECT_DIR 未设置或其下无 kag_config.yaml（Bridge 以项目目录的配置文件为唯一配置源）"
            )
        # KAGConfigAccessor 读取 cwd 下的 kag_config.yaml（M0-1 验证的加载方式）
        os.chdir(KAG_PROJECT_DIR)
        from kag.common.conf import KAGConfigAccessor

        cfg = KAGConfigAccessor.get_config().all_config
        if not cfg or "llm" not in cfg or "project" not in cfg:
            raise RuntimeError("kag_config.yaml 缺少 llm / project 配置")
        _MAIN_CONFIG = cfg
        _KAG_READY = True


def _project_info() -> dict:
    p = _MAIN_CONFIG.get("project", {}) or {}
    return {
        "namespace": str(p.get("namespace", "")),
        "project_id": str(p.get("id", "")),
        "host_addr": str(p.get("host_addr", "")),
    }


def _build_reporter(task_id: str, project_id: str, on_event):
    """继承 OpenSPGReporter 的事件桥接 reporter（host_addr=None 零网络）。

    on_event(text) 为同步回调：状态变化时触发（粗粒度进度）。
    """
    from kag.solver.reporter.open_spg_reporter import OpenSPGReporter

    class _BridgeReporter(OpenSPGReporter):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            self.last_status = {}

        def add_report_line(self, segment, tag_name, content, status, **kwargs):
            super().add_report_line(segment, tag_name, content, status, **kwargs)
            key = f"{segment}/{tag_name}"
            if self.last_status.get(key) != status:  # 仅状态变化上报，防刷屏
                self.last_status[key] = status
                try:
                    on_event(f"[{segment}/{tag_name}] {status}")
                except Exception:
                    pass

        def do_report(self):
            pass  # 终态产物（StreamData）在 kag_solve 结束时统一组装

    return _BridgeReporter(task_id=task_id, host_addr=None, project_id=project_id)


def _schedule_info(ctx, loop, text: str) -> None:
    """同步回调里异步发 ctx.info（fire-and-forget，失败静默）。"""

    def _run():
        try:
            asyncio.ensure_future(ctx.info(text))
        except Exception:
            pass

    try:
        loop.call_soon_threadsafe(_run)
    except Exception:
        pass


@mcp.tool()
async def kag_solve(
    question: str,
    project_id: str = "",
    use_pipeline: str = "think_pipeline",
    ctx: Context = None,
) -> str:
    """对绑定的 KAG 项目执行 LLM 增强推理，返回答案与引用（含 subgraph 轨迹数据）。

    无状态单问单答：多轮上下文由调用方（agent loop）负责拼装进 question，需携带前文。
    project_id 可省略（缺省用 Bridge 绑定的项目；传入其他 id 会返回结构化错误——多项目路由 M2 提供）。
    """
    _ensure_kag()
    info = _project_info()
    if project_id and project_id != info["project_id"]:
        return json.dumps(
            {"error": f"Bridge 绑定项目 {info['project_id']}（namespace={info['namespace']}），"
            f"project_id={project_id} 的多项目路由于 M2 提供"},
            ensure_ascii=False,
        )
    if use_pipeline not in ("think_pipeline", "default_pipeline", "index_pipeline"):
        return json.dumps({"error": f"未知 use_pipeline: {use_pipeline}"}, ensure_ascii=False)

    from kag.solver.main_solver import do_qa_pipeline

    async with _solve_semaphore:
        task_id = f"bridge_{int(time.time() * 1000)}"
        loop = asyncio.get_running_loop()
        reporter = _build_reporter(
            task_id, info["project_id"], lambda t: _schedule_info(ctx, loop, t)
        )
        if ctx:
            await ctx.info(f"kag_solve 开始（namespace={info['namespace']}）")
        t0 = time.time()
        try:
            answer = await do_qa_pipeline(
                use_pipeline,
                question,
                _MAIN_CONFIG,
                reporter,
                task_id=task_id,
                kb_project_ids=[],
            )
        finally:
            # M0-1 陷阱 2：异常路径也必须 stop，否则 asyncio 清理死锁
            await reporter.stop()
        cost_ms = int((time.time() - t0) * 1000)

        # 终态产物组装（StreamData：answer/think/reference/subgraph/metrics——附录 A.2 数据模型）
        stream_data = {}
        try:
            content, _status, _metrics = reporter.generate_report_data()
            stream_data = content.to_dict()
        except Exception:
            pass
        if ctx:
            await ctx.report_progress(1, 1)

        return json.dumps(
            {
                "answer": str(answer),
                "reference": stream_data.get("reference", []),
                "subgraph": stream_data.get("subgraph", []),
                "cost_ms": cost_ms,
                "namespace": info["namespace"],
            },
            ensure_ascii=False,
            default=str,
        )


@mcp.tool()
async def kag_schema(project_id: str = "", ctx: Context = None) -> str:
    """查询绑定 KAG 项目的 Schema 摘要（只读：SPG type 清单）。"""
    _ensure_kag()
    info = _project_info()
    if project_id and project_id != info["project_id"]:
        return json.dumps({"error": f"Bridge 绑定项目 {info['project_id']}，多项目路由于 M2 提供"}, ensure_ascii=False)

    from knext.reasoner.client import ReasonerClient

    rc = ReasonerClient(host_addr=info["host_addr"], project_id=int(info["project_id"]))
    schema = rc.get_reason_schema()
    return json.dumps(
        {
            "project_id": info["project_id"],
            "namespace": info["namespace"],
            "spg_types": {
                name: {"spg_type_enum": str(getattr(v, "spg_type_enum", ""))}
                for name, v in schema.items()
            },
        },
        ensure_ascii=False,
        default=str,
    )


@mcp.tool()
async def kag_status(ctx: Context = None) -> str:
    """Bridge 与 OpenSPG server 的健康/连通性检查（不回显任何凭据）。"""
    _ensure_kag()
    info = _project_info()

    def _probe_server() -> tuple[bool, str]:
        try:
            req = urllib.request.Request(f"{info['host_addr'].rstrip('/')}/public/v1/project", method="GET")
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status == 200, f"HTTP {resp.status}"
        except Exception as exc:  # noqa: BLE001 - 健康探测需把任何失败转为状态
            return False, repr(exc)[:200]

    # 同步 urllib 放线程池，避免阻塞事件循环
    server_ok, detail = await asyncio.to_thread(_probe_server)
    return json.dumps(
        {
            "bridge": "ok",
            "kag_project_dir": KAG_PROJECT_DIR,
            "namespace": info["namespace"],
            "project_id": info["project_id"],
            "spg_server": {"url": info["host_addr"], "reachable": server_ok, "detail": detail},
            "llm_configured": bool(_MAIN_CONFIG.get("llm")),
            "vectorizer_configured": bool(_MAIN_CONFIG.get("vectorizer") or _MAIN_CONFIG.get("vectorize_model")),
        },
        ensure_ascii=False,
    )


def main() -> None:
    if not KAG_PROJECT_DIR:
        # stderr，绝不能 print 到 stdout——stdio transport 下 stdout 是协议通道
        print("WARN: KAG_PROJECT_DIR 未设置，KAG 工具将在调用时报错", file=sys.stderr, flush=True)
    mcp.run()  # stdio transport


if __name__ == "__main__":
    main()
