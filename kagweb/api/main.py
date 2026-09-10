from contextlib import asynccontextmanager
import logging
import sys

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from kagweb.logging import configure_logging
from kagweb.services.config import (
    ensure_runtime_settings_files,
    export_runtime_settings_to_env,
    load_auth_settings,
    load_system_settings,
)
from kagweb.services.config.origins import normalize_origins
from kagweb.services.path_service import get_path_service

ensure_runtime_settings_files()
export_runtime_settings_to_env(overwrite=True)
configure_logging()
logger = logging.getLogger(__name__)


class _SuppressWsNoise(logging.Filter):
    """Suppress noisy uvicorn logs for WebSocket connection churn."""

    _SUPPRESSED = ("connection open", "connection closed")

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        return not any(f in msg for f in self._SUPPRESSED)


logging.getLogger("uvicorn.error").addFilter(_SuppressWsNoise())


def _build_cors_settings() -> dict[str, object]:
    """Build CORS settings for both localhost and remote Docker deployments."""
    system_settings = load_system_settings()
    auth_settings = load_auth_settings()
    frontend_port = str(system_settings["frontend_port"])
    extra_origins = normalize_origins(
        [system_settings["cors_origin"], system_settings["cors_origins"]]
    )
    origins = [
        f"http://localhost:{frontend_port}",
        f"http://127.0.0.1:{frontend_port}",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ]
    for origin in extra_origins:
        if origin not in origins:
            origins.append(origin)

    # Auth is disabled by default. In that local/single-user mode, mirror the
    # pre-v1.3.8 behavior and allow remote Docker/LAN origins out of the box.
    # When auth is enabled, require explicit CORS_ORIGIN(S) for credentialed
    # cross-origin requests.
    allow_origin_regex = None if auth_settings["enabled"] else r"https?://.*"
    mode = "explicit" if auth_settings["enabled"] else "permissive"
    return {
        "allow_origins": origins,
        "allow_origin_regex": allow_origin_regex,
        "mode": mode,
    }


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifecycle management
    Gracefully handle startup and shutdown events, avoid CancelledError
    """
    # Execute on startup
    logger.info("Application startup")
    app.state.ready = False

    # Build one process-level dependency graph.  Every adapter resolves turns
    # through this container, so a WebSocket cannot accidentally construct a
    # second store/runtime for the same authenticated scope.
    from kagweb.app.container import get_application_container

    application_container = getattr(app.state, "application_container", None)
    if application_container is None:
        application_container = get_application_container()
        app.state.application_container = application_container
    await application_container.start()
    migration_reports = await application_container.run_startup_data_migrations()
    legacy_reports = migration_reports["legacy_chat"]
    migrated_reports = [report for report in legacy_reports if report.get("source_hash")]
    if migrated_reports:
        logger.info(
            "Legacy chat migration complete: imported=%s skipped=%s archived=%s",
            sum(int(report.get("imported") or 0) for report in migrated_reports),
            sum(int(report.get("skipped") or 0) for report in migrated_reports),
            [report.get("archived_to", "") for report in migrated_reports],
        )
    workspace_migrated = sum(
        int(report.get("migrated") or 0) for report in migration_reports["workspace_preferences"]
    )
    if workspace_migrated:
        logger.info(
            "Workspace session migration complete: migrated=%s scopes=%s",
            workspace_migrated,
            len(migration_reports["workspace_preferences"]),
        )

    # Initialize LLM client early so OPENAI_* env vars are available before
    # any downstream provider integrations start.
    try:
        from kagweb.services.llm import get_llm_client

        llm_client = get_llm_client()
        logger.info(f"LLM client initialized: model={llm_client.config.model}")
    except Exception as e:
        logger.warning(f"Failed to initialize LLM client at startup: {e}")

    try:
        from kagweb.events.event_bus import get_event_bus

        event_bus = get_event_bus()
        await event_bus.start()
        logger.info("EventBus started")
    except Exception as e:
        logger.warning(f"Failed to start EventBus: {e}")

    async def _start_partners() -> None:
        from kagweb.services.partners import get_partner_manager

        manager = get_partner_manager()
        manager.configure_runtime_owner(application_container.worker_id)
        await manager.auto_start_partners()

    async def _stop_partners() -> None:
        from kagweb.services.partners import get_partner_manager

        await get_partner_manager().stop_all(preserve_auto_start=True)

    from kagweb.runtime.coordination import BackgroundCommandKind

    async def _handle_background_command(command) -> None:
        kind = str(command.kind)

        from kagweb.services.partners import get_partner_manager

        manager = get_partner_manager()
        manager.configure_runtime_owner(application_container.worker_id)
        partner_id = str(command.payload.get("partner_id") or "").strip()
        if not partner_id:
            raise ValueError(f"Background command {kind!r} needs partner_id")
        if kind == BackgroundCommandKind.PARTNER_START:
            instance = await manager.start_partner(partner_id)
            if bool(command.payload.get("persist_auto_start", True)):
                manager.save_config(partner_id, instance.config, auto_start=True)
            return
        if kind == BackgroundCommandKind.PARTNER_STOP:
            await manager.stop_partner(
                partner_id,
                preserve_auto_start=bool(command.payload.get("preserve_auto_start", False)),
            )
            return
        if kind == BackgroundCommandKind.PARTNER_RELOAD:
            instance = manager.get_partner(partner_id)
            if instance is None or not instance.running:
                return
            config = manager.load_config(partner_id)
            if config is not None:
                instance.config = config
            await manager.reload_channels(partner_id)
            return
        raise ValueError(f"Unknown background command kind: {kind}")

    from kagweb.runtime.background_leader import BackgroundLeaderSupervisor

    background_supervisor = BackgroundLeaderSupervisor(
        application_container.coordinator,
        application_container.worker_id,
        start_callbacks=[_start_partners],
        stop_callbacks=[_stop_partners],
        recovery_callback=application_container.recover_once,
        control_callback=_handle_background_command,
        renew_interval_seconds=application_container.settings.renew_interval_seconds,
        recovery_interval_seconds=application_container.settings.recovery_interval_seconds,
    )
    app.state.background_supervisor = background_supervisor
    await background_supervisor.start()

    # Ping PocketBase if configured — logs a warning (not an error) if unreachable
    try:
        from kagweb.services.pocketbase_client import ping_pocketbase

        await ping_pocketbase()
    except Exception as e:
        logger.warning(f"PocketBase startup check failed: {e}")

    app.state.ready = True
    yield

    # Execute on shutdown
    app.state.ready = False
    logger.info("Application shutdown")

    try:
        await background_supervisor.close()
        logger.info("Leader-owned background services stopped")
    except Exception as e:
        logger.warning(f"Failed to stop background leader: {e}")

    try:
        await application_container.close()
        logger.info("Application container closed")
    except Exception as e:
        logger.warning(f"Failed to close application container: {e}")

    # Close MCP server connections. Each one owns an AsyncExitStack inside its
    # own task, so they must be torn down here rather than left to interpreter
    # exit (stdio servers would otherwise leak child processes).
    try:
        from kagweb.services.mcp import get_mcp_manager

        await get_mcp_manager().shutdown()
        logger.info("MCP connections closed")
    except Exception as e:
        logger.warning(f"Failed to close MCP connections: {e}")

    # Close pooled LLM SDK clients so their keep-alive sockets and transports
    # are released deterministically instead of waiting for interpreter GC.
    try:
        from kagweb.services.llm.provider_factory import close_runtime_provider_pool

        await close_runtime_provider_pool()
        logger.info("LLM provider pool closed")
    except Exception as e:
        logger.warning(f"Failed to close LLM provider pool: {e}")

    # Stop ACP agent-loop children (one per recently-active session). Without
    # this they survive the server as orphans — the lazy reaper only runs on
    # the next acquire, which never comes after shutdown.
    try:
        from kagweb.services.agent_loop.acp_backend import shutdown_all_acp_sessions

        await shutdown_all_acp_sessions()
        logger.info("ACP agent-loop children closed")
    except Exception as e:
        logger.warning(f"Failed to close ACP agent-loop children: {e}")

    # Stop EventBus
    try:
        from kagweb.events.event_bus import get_event_bus

        event_bus = get_event_bus()
        await event_bus.stop()
        logger.info("EventBus stopped")
    except Exception as e:
        logger.warning(f"Failed to stop EventBus: {e}")


app = FastAPI(
    title="KAGWeb API",
    version="1.0.0",
    lifespan=lifespan,
    # Disable automatic trailing slash redirects to prevent protocol downgrade issues
    # when deployed behind HTTPS reverse proxies (e.g., nginx).
    # Without this, FastAPI's 307 redirects may change HTTPS to HTTP.
    # See: upstream issue #112
    redirect_slashes=False,
)


@app.middleware("http")
async def json_error_boundary(request: Request, call_next):
    """Catch-all so 500s always return JSON, never Starlette's plain-text body.

    Registered as a middleware rather than an ``@app.exception_handler``: a
    handler for ``Exception`` is installed on Starlette's outermost
    ``ServerErrorMiddleware``, so its response skips every middleware added
    here — the 500 would carry no CORS headers (a cross-origin caller sees an
    opaque CORS failure instead of this body) and would never reach the access
    log below. Registered *before* ``CORSMiddleware``, this boundary sits
    inside it, so the response travels back out through the normal stack.
    """
    try:
        return await call_next(request)
    except Exception as exc:
        logger.error(
            "Unhandled exception on %s %s: %s",
            request.method,
            request.url.path,
            exc,
            exc_info=True,
        )
        return JSONResponse(
            status_code=500,
            content={
                "detail": f"{type(exc).__name__}: {exc}",
                "type": type(exc).__name__,
            },
        )


# Access logging is funneled through this one middleware. uvicorn's own
# per-request access log is disabled on every launch path (run_server.py via
# access_log=False; the launcher and Docker via `--no-access-log`), so routine
# 200s — the chatty frontend polling of /settings, /tools, /knowledge-bases,
# etc. — never reach the logs. Only non-200s are surfaced, since those are the
# ones worth seeing.
#
# The `kagweb.access` logger gets its own INFO stdout handler rather than
# leaning on the root handlers: the root console handler runs at the global log
# level (WARNING by default), which would swallow these INFO access lines.
# propagate=False keeps them from also printing through root if the global
# level is ever lowered to INFO/DEBUG.
_access_logger = logging.getLogger("kagweb.access")
if not any(getattr(h, "_kagweb_access_handler", False) for h in _access_logger.handlers):
    _access_handler = logging.StreamHandler(sys.stdout)
    _access_handler.setLevel(logging.INFO)
    _access_handler.setFormatter(logging.Formatter("%(message)s"))
    _access_handler._kagweb_access_handler = True  # type: ignore[attr-defined]
    _access_logger.addHandler(_access_handler)
    _access_logger.setLevel(logging.INFO)
    _access_logger.propagate = False


@app.middleware("http")
async def selective_access_log(request, call_next):
    response = await call_next(request)
    if response.status_code != 200:
        _access_logger.info(
            '%s - "%s %s HTTP/%s" %d',
            request.client.host if request.client else "-",
            request.method,
            request.url.path,
            request.scope.get("http_version", "1.1"),
            response.status_code,
        )
    return response


_cors_settings = _build_cors_settings()
logger.info(
    "CORS configured: mode=%s allow_origins=%s allow_origin_regex=%s",
    _cors_settings["mode"],
    _cors_settings["allow_origins"],
    _cors_settings["allow_origin_regex"],
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_settings["allow_origins"],
    allow_origin_regex=_cors_settings["allow_origin_regex"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize user directories on startup
try:
    from kagweb.services.setup import init_user_directories

    init_user_directories()
except Exception:
    # Fallback: just create the main directory if it doesn't exist
    user_dir = get_path_service().get_public_outputs_root()
    if not user_dir.exists():
        user_dir.mkdir(parents=True)

# Import routers only after runtime settings are initialized.
# Some router modules load YAML settings at import time.
from kagweb.api.routers import (
    attachments,
    auth,
    capabilities,
    capabilities_settings,
    imports,
    mcp_settings,
    outputs,
    partner_groups,
    partners,
    personas,
    sessions,
    settings,
    space_mcp,
    system,
    unified_ws,
    voice,
)
from kagweb.api.routers.multi_user import router as multi_user_router  # noqa: E402

# Auth router is public — login/logout/register/status require no token
app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
app.include_router(outputs.router, prefix="/files/outputs", tags=["outputs"])

# All other routers require a valid session when AUTH_ENABLED=true.
# require_auth is a no-op when AUTH_ENABLED=false, so this is safe for local use.
from kagweb.api.routers.auth import (  # noqa: E402
    require_learning_surface,
)

_auth = [Depends(require_learning_surface)]

app.include_router(
    multi_user_router,
    prefix="/api/multi-user",
    tags=["multi-user"],
    dependencies=_auth,
)

app.include_router(imports.router, prefix="/api/imports", tags=["imports"], dependencies=_auth)
app.include_router(
    capabilities_settings.router,
    prefix="/api/capabilities",
    tags=["capabilities"],
    dependencies=_auth,
)
app.include_router(
    capabilities.router,
    prefix="/api/capabilities",
    tags=["capabilities"],
    dependencies=_auth,
)
app.include_router(sessions.router, prefix="/api/sessions", tags=["sessions"], dependencies=_auth)
# Public UI-settings read (auth pages bootstrap the interface language
# before a session exists, so GET /api/settings/ui must not be gated
# by _auth). Mounted first so the path resolves here, not on the gated
# settings router below.
app.include_router(
    settings.public_router,
    prefix="/api/settings",
    tags=["settings"],
)
app.include_router(settings.router, prefix="/api/settings", tags=["settings"], dependencies=_auth)
app.include_router(
    mcp_settings.router,
    prefix="/api/settings/mcp",
    tags=["mcp-settings"],
    dependencies=_auth,
)
# Per-user MCP servers. Deliberately only ``_auth``: the router's own routes
# resolve the owner server-side, and everything a non-admin can reach through it
# is remote-transport-only (see the module docstring). The admin registry above
# keeps its own ``require_admin``.
app.include_router(
    space_mcp.router,
    prefix="/api/space/mcp",
    tags=["space-mcp"],
    dependencies=_auth,
)
app.include_router(personas.router, prefix="/api", tags=["personas"], dependencies=_auth)
app.include_router(system.router, prefix="/api/system", tags=["system"], dependencies=_auth)
app.include_router(voice.router, prefix="/api/voice", tags=["voice"], dependencies=_auth)
# Partners are per-user resources now: anyone may build their own, and an admin
# may assign theirs to others. Only ``_auth`` here — every route in the router
# declares whether it needs *use* or *manage* rights on the partner it names
# (see ``multi_user.partner_access``), which a blanket admin gate could not
# express.
app.include_router(partners.router, prefix="/api/partners", tags=["partners"], dependencies=_auth)
app.include_router(
    partner_groups.router,
    prefix="/api/partner-groups",
    tags=["partner-groups"],
    dependencies=_auth,
)
app.include_router(partners.ws_router, prefix="/ws/partners", tags=["partners"])
app.include_router(
    partner_groups.ws_router,
    prefix="/ws/partner-groups",
    tags=["partner-groups"],
)
app.include_router(
    attachments.router,
    prefix="/files/attachments",
    tags=["attachments"],
    dependencies=_auth,
)

# Unified WebSocket endpoint — auth is checked inside the handler (WebSockets
# cannot use FastAPI dependencies in the standard way)
app.include_router(unified_ws.router, tags=["unified-ws"])


@app.get("/")
async def root():
    return {"message": "Welcome to KAGWeb API"}


@app.get("/health/live")
async def health_live():
    return {"status": "alive"}


@app.get("/health/ready")
async def health_ready(request: Request):
    if not bool(getattr(request.app.state, "ready", False)):
        return JSONResponse(status_code=503, content={"status": "not_ready"})
    container = getattr(request.app.state, "application_container", None)
    if container is None or not await container.coordinator.health():
        return JSONResponse(
            status_code=503,
            content={"status": "not_ready", "reason": "coordination_unavailable"},
        )
    return {"status": "ready"}


if __name__ == "__main__":
    from kagweb.api.run_server import main as run_server_main

    run_server_main()
