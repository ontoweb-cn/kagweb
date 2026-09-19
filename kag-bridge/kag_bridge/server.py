# -*- coding: utf-8 -*-
"""KAG Bridge — MCP Server（M1 stdio + M3.1 streamable-http）。

设计文档：docs/kag-integration-design.md §5.1 + 附录 A（D1：双 transport）。
单项目 MVP：Bridge 实例经 KAG_PROJECT_DIR 绑定一个 KAG 项目；显式传入
不同 project_id 返回结构化错误（多项目路由 M2 提供）。

transport 选择（KAG_BRIDGE_TRANSPORT）：
  - stdio（缺省）：Claude Code / Codex 子进程接入；
  - http：FastMCP streamable-http（缺省 127.0.0.1:8890/mcp），供 Intellect
    等带原生 MCP 客户端的 HTTP agent loop 接入；Bearer 鉴权强制——
    未配置 KAG_BRIDGE_API_KEY 时拒绝启动（内网监听 + key 双防线）。

内建的 M0/M3.0 实测结论（勿改）：
  1. 配置唯一来源 = KAG_PROJECT_DIR/kag_config.yaml；禁止运行时执行
     KAG_PROJECT_CONF.host_addr = <env>（会丢 all_config 的 llm 键）；
  2. reporter 生命周期必须 try/finally 包 reporter.stop()（do_cycle_report
     吞 CancelledError，异常路径不 stop 会死锁 asyncio 清理并掩盖 traceback）；
  3. OpenSPGReporter(host_addr=None) 纯内存零网络，add_report_line 无副作用；
  4. kag_config.yaml 必须含自定义 think_pipeline: 顶层键（do_qa_pipeline 用
     kb 派生列表覆盖顶层 retrievers，模板路径检索恒空——M3.0 根因①）。
"""

import asyncio
import hmac
import json
import os
import sys
import threading
import time
import urllib.request
from pathlib import Path

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.transport_security import TransportSecuritySettings

KAG_PROJECT_DIR = os.environ.get("KAG_PROJECT_DIR", "").strip()

# Host 头放行表：FastMCP 对 loopback 绑定自动开启 DNS-rebinding 防护（默认仅
# 127.0.0.1/localhost/[::1]），容器化调用方（Intellect 等）以
# host.docker.internal 访问宿主 Bridge 会被 421 拒绝。KAG_BRIDGE_EXTRA_HOSTS
# 逗号分隔追加（如内网域名），绑定地址本身始终放行。
_extra_hosts = [h.strip() for h in os.environ.get("KAG_BRIDGE_EXTRA_HOSTS", "").split(",") if h.strip()]
_bind_host = os.environ.get("KAG_BRIDGE_HTTP_HOST", "127.0.0.1").strip()
_allowed_hosts = ["127.0.0.1:*", "localhost:*", "[::1]:*", "host.docker.internal:*", f"{_bind_host}:*"]
_allowed_hosts += [h if ":*" in h else f"{h}:*" for h in _extra_hosts]

mcp = FastMCP(
    "kag-bridge",
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=_allowed_hosts,
        allowed_origins=[
            "http://127.0.0.1:*",
            "http://localhost:*",
            "http://[::1]:*",
            "http://host.docker.internal:*",
        ],
    ),
)

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

        # 任务摘要上报（A.3；异步放行，不阻塞工具返回）
        loop.call_soon_threadsafe(
            lambda: asyncio.ensure_future(
                asyncio.to_thread(
                    _report_task,
                    {
                        "task_id": task_id,
                        "session_id": os.environ.get("KAG_SESSION_ID", ""),
                        "project_id": info["project_id"],
                        "namespace": info["namespace"],
                        "question": question[:2000],
                        "answer_digest": str(answer)[:2000],
                        "cost_ms": cost_ms,
                        "references": stream_data.get("reference", [])[:20],
                    },
                )
            )
        )
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


def _report_task(payload: dict) -> None:
    """任务摘要上报（附录 A.3）：KAGWEB_API_URL + KAG_BRIDGE_API_KEY 均
    配置时才启用；fire-and-forget，失败仅记 stderr（不影响答案返回）。"""
    import urllib.error

    url = os.environ.get("KAGWEB_API_URL", "").rstrip("/")
    key = os.environ.get("KAG_BRIDGE_API_KEY", "")
    if not url or not key:
        return
    try:
        req = urllib.request.Request(
            f"{url}/api/kag/bridge/tasks",
            data=json.dumps(payload, ensure_ascii=False, default=str).encode(),
            method="POST",
            headers={"Content-Type": "application/json", "X-KAG-Bridge-Key": key},
        )
        urllib.request.urlopen(req, timeout=5).read()
    except Exception as exc:  # noqa: BLE001 - 上报失败不阻塞工具结果
        print(f"WARN: kag task report failed: {exc!r}", file=sys.stderr)


class _BearerAuthMiddleware:
    """纯 ASGI 中间件：http transport 全端点 Bearer 鉴权（/healthz 豁免）。

    不用 Starlette BaseHTTPMiddleware——它对流式响应（streamable-http 的
    POST 返回为 SSE/流式 JSON）有缓冲副作用；纯 ASGI 包装对任意响应安全。
    hmac.compare_digest 防时序侧信道。
    """

    def __init__(self, app, api_key: str):
        self.app = app
        self.api_key = api_key

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and not scope.get("path", "").startswith("/healthz"):
            auth = ""
            for k, v in scope.get("headers", []):
                if k.decode("latin-1").lower() == "authorization":
                    auth = v.decode("latin-1")
                    break
            expected = f"Bearer {self.api_key}"
            if not hmac.compare_digest(auth, expected):
                body = json.dumps(
                    {"error": "unauthorized", "hint": "Authorization: Bearer <KAG_BRIDGE_API_KEY>"},
                    ensure_ascii=False,
                ).encode()
                await send(
                    {
                        "type": "http.response.start",
                        "status": 401,
                        "headers": [
                            (b"content-type", b"application/json"),
                            (b"www-authenticate", b"Bearer"),
                        ],
                    }
                )
                await send({"type": "http.response.body", "body": body})
                return
        await self.app(scope, receive, send)


def _run_http() -> None:
    """streamable-http transport（附录 A.2，D1）。"""
    api_key = os.environ.get("KAG_BRIDGE_API_KEY", "").strip()
    if not api_key:
        raise SystemExit("KAG_BRIDGE_TRANSPORT=http 时必须设置 KAG_BRIDGE_API_KEY（拒绝无鉴权启动）")
    host = os.environ.get("KAG_BRIDGE_HTTP_HOST", "127.0.0.1")
    port = int(os.environ.get("KAG_BRIDGE_HTTP_PORT", "8890"))
    mcp.settings.host = host
    mcp.settings.port = port
    mcp.settings.streamable_http_path = os.environ.get("KAG_BRIDGE_HTTP_PATH", "/mcp")
    # 无状态单发工具服务（kag_solve 单问单答），不依赖服务端会话保持
    mcp.settings.stateless_http = True

    app = _BearerAuthMiddleware(mcp.streamable_http_app(), api_key)

    async def _healthz(request):
        from starlette.responses import JSONResponse

        return JSONResponse({"status": "ok", "transport": "streamable-http"})

    from starlette.routing import Route

    app.app.routes.append(Route("/healthz", _healthz, methods=["GET"]))

    import uvicorn

    uvicorn.run(app, host=host, port=port, log_level="info")


def main() -> None:
    if not KAG_PROJECT_DIR:
        # stderr，绝不能 print 到 stdout——stdio transport 下 stdout 是协议通道
        print("WARN: KAG_PROJECT_DIR 未设置，KAG 工具将在调用时报错", file=sys.stderr, flush=True)
    transport = os.environ.get("KAG_BRIDGE_TRANSPORT", "stdio").strip().lower()
    if transport == "http":
        _run_http()
    else:
        mcp.run()  # stdio transport


if __name__ == "__main__":
    main()
