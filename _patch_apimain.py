"""One-shot Phase 1 patch: api/main.py startup hooks + router mounts."""
import io
import py_compile

path = "kagweb/api/main.py"
with io.open(path, "r", encoding="utf-8") as f:
    src = f.read()


def cut(src, start, end):
    i = src.index(start)
    j = src.index(end, i)
    return src[:i] + src[j:]


def rep(src, old, new, count=1):
    assert old in src, f"NOT FOUND: {old[:90]!r}"
    return src.replace(old, new, count)


# 1) knowledge progress ports install
src = rep(
    src,
    """    from kagweb.api.utils.progress_broadcaster import ProgressBroadcaster
    from kagweb.api.utils.task_log_stream import get_task_stream_manager
    from kagweb.knowledge.progress_events import install_progress_ports

    install_progress_ports(
        broadcast=ProgressBroadcaster.get_instance().broadcast,
        emit_task_event=get_task_stream_manager().emit,
    )
    migration_reports""",
    """    migration_reports""",
)

# 2) cron + github sync lifecycle helpers
src = rep(
    src,
    """    async def _start_cron() -> None:
        from kagweb.services.cron import get_cron_service

        await get_cron_service().start()

    async def _stop_cron() -> None:
        from kagweb.services.cron import get_cron_service

        await get_cron_service().stop()

    async def _start_github_sync() -> None:
        from kagweb.services.github_source.sync_service import get_sync_service

        await get_sync_service().start()

    async def _stop_github_sync() -> None:
        from kagweb.services.github_source.sync_service import get_sync_service

        await get_sync_service().stop()

    from kagweb.runtime.coordination import BackgroundCommandKind

    async def _handle_background_command(command) -> None:
        kind = str(command.kind)
        if kind == BackgroundCommandKind.CRON_RELOAD:
            from kagweb.services.cron import get_cron_service

            get_cron_service().reload()
            return

        from kagweb.services.partners import get_partner_manager""",
    """    from kagweb.runtime.coordination import BackgroundCommandKind

    async def _handle_background_command(command) -> None:
        kind = str(command.kind)

        from kagweb.services.partners import get_partner_manager""",
)

# 3) cron change notifier wiring
src = cut(
    src,
    """    cron_service = None
    try:
        from kagweb.services.cron import get_cron_service""",
    """    from kagweb.runtime.background_leader import BackgroundLeaderSupervisor""",
)
src = rep(
    src,
    """        start_callbacks=[_start_partners, _start_cron, _start_github_sync],
        stop_callbacks=[_stop_partners, _stop_cron, _stop_github_sync],""",
    """        start_callbacks=[_start_partners],
        stop_callbacks=[_stop_partners],""",
)

# 4) memory migration startup block
src = rep(
    src,
    """    # Migrate any v1 memory files (PROFILE.md / SOUL.md / SUMMARY.md) into a
    # backup folder so the v2 three-layer subsystem starts clean.
    try:
        from kagweb.services.memory import (
            migrate_partner_surface_if_needed,
            migrate_v1_if_needed,
        )
        from kagweb.services.path_service import get_path_service

        get_path_service().migrate_legacy_memory_markdown()
        backup = migrate_v1_if_needed()
        if backup is not None:
            logger.info("v1 memory archived to %s", backup)
        # Rename the legacy ``tutorbot`` memory surface (footnote refs, L2
        # doc, snapshot/trace dirs, L3 meta keys) to ``partner``.
        migrate_partner_surface_if_needed()
    except Exception as e:
        logger.warning(f"v1 memory migration failed: {e}")

    app.state.ready = True""",
    """    app.state.ready = True""",
)

# 5) shutdown reset of progress ports
src = rep(
    src,
    """    logger.info("Application shutdown")

    install_progress_ports(broadcast=None, emit_task_event=None)

    try:""",
    """    logger.info("Application shutdown")

    try:""",
)

# 6) router imports
src = rep(
    src,
    """from kagweb.api.routers import (
    agent_config,
    attachments,
    auth,
    book,
    capabilities,
    capabilities_settings,
    co_writer,
    courses,
    dashboard,
    imports,
    knowledge,
    marginnote4,
    mastery_path,
    mcp_settings,
    memory,
    notebook,
    outputs,
    partner_groups,
    partners,
    personas,
    question,
    question_notebook,
    quiz_judge,
    reading,
    reading_extensions,
    sessions,
    settings,
    skills,
    space_cli_apps,
    space_mcp,
    subagents,
    system,
    unified_ws,
    video_learning,
    visualizers,
    voice,
)""",
    """from kagweb.api.routers import (
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
)""",
)

# 7) mounts
src = rep(
    src,
    """app.include_router(question.router, prefix="/api/question", tags=["question"], dependencies=_auth)
app.include_router(knowledge.router, prefix="/api", tags=["knowledge-bases"], dependencies=_auth)
app.include_router(imports.router, prefix="/api/imports", tags=["imports"], dependencies=_auth)
app.include_router(
    dashboard.router, prefix="/api/dashboard", tags=["dashboard"], dependencies=_auth
)
app.include_router(
    mastery_path.router,
    prefix="/api/mastery-paths",
    tags=["mastery-path"],
    dependencies=_auth,
)
# WebSocket handlers authenticate inside the connection before ``accept``.
# Keep them off HTTP router dependencies: ``require_learning_surface`` takes a
# Request, which FastAPI cannot construct for a WebSocket scope.
app.include_router(question.ws_router, prefix="/ws/questions", tags=["question"])
app.include_router(knowledge.ws_router, prefix="/ws", tags=["knowledge-bases"])
app.include_router(
    mastery_path.ws_router,
    prefix="/ws",
    tags=["mastery-path"],
)
app.include_router(co_writer.router, prefix="/api", tags=["documents"], dependencies=_auth)
app.include_router(notebook.router, prefix="/api", tags=["notebooks"], dependencies=_auth)
app.include_router(book.router, prefix="/api", tags=["books"], dependencies=_auth)
app.include_router(book.ws_router, prefix="/ws", tags=["books"])
app.include_router(reading.router, prefix="/api/reading", tags=["reading"], dependencies=_auth)
app.include_router(
    reading_extensions.router,
    prefix="/api/reading",
    tags=["reading-extensions"],
    dependencies=_auth,
)
app.include_router(memory.router, prefix="/api/memory", tags=["memory"], dependencies=_auth)
app.include_router(""",
    """app.include_router(imports.router, prefix="/api/imports", tags=["imports"], dependencies=_auth)
app.include_router(""",
)

src = rep(
    src,
    """app.include_router(sessions.router, prefix="/api/sessions", tags=["sessions"], dependencies=_auth)
app.include_router(courses.router, prefix="/api/courses", tags=["courses"], dependencies=_auth)
app.include_router(
    question_notebook.router,
    prefix="/api/question-notebook",
    tags=["question-notebook"],
    dependencies=_auth,
)
# Public UI-settings read""",
    """app.include_router(sessions.router, prefix="/api/sessions", tags=["sessions"], dependencies=_auth)
# Public UI-settings read""",
)

src = rep(
    src,
    """app.include_router(
    video_learning.settings_router,
    prefix="/api/settings/video-learning",
    tags=["video-learning-settings"],
    dependencies=_admin,
)
app.include_router(""",
    """app.include_router(""",
)

src = rep(
    src,
    """app.include_router(skills.router, prefix="/api/skills", tags=["skills"], dependencies=_auth)
app.include_router(
    subagents.router, prefix="/api/subagents", tags=["subagents"], dependencies=_auth
)
app.include_router(personas.router, prefix="/api", tags=["personas"], dependencies=_auth)""",
    """app.include_router(personas.router, prefix="/api", tags=["personas"], dependencies=_auth)""",
)

src = rep(
    src,
    """app.include_router(voice.router, prefix="/api/voice", tags=["voice"], dependencies=_auth)
app.include_router(
    video_learning.router,
    prefix="/api/video-learning",
    tags=["video-learning"],
    dependencies=_auth,
)
app.include_router(
    visualizers.router,
    prefix="/api/visualizers",
    tags=["visualizers"],
    dependencies=_auth,
)
app.include_router(
    agent_config.router, prefix="/api/agent-config", tags=["agent-config"], dependencies=_auth
)
# Partners are per-user resources now""",
    """app.include_router(voice.router, prefix="/api/voice", tags=["voice"], dependencies=_auth)
# Partners are per-user resources now""",
)

src = rep(
    src,
    """app.include_router(
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

# MarginNote 4 device bridge — pairing/management routes carry _auth in-router;
# sync/heartbeat use device-token auth (the Add-on has no session).
app.include_router(
    marginnote4.router,
    prefix="/api/marginnote4",
    tags=["marginnote4"],
)

# Unified WebSocket endpoint — auth is checked inside the handler (WebSockets
# cannot use FastAPI dependencies in the standard way)
app.include_router(unified_ws.router, tags=["unified-ws"])

# Quiz AI-judge WebSocket — same caveat as unified_ws above; auth is checked
# inside the handler so the WS upgrade isn't rejected by an HTTP-style dep.
app.include_router(quiz_judge.router, prefix="/ws", tags=["quiz-judge"])""",
    """app.include_router(
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
app.include_router(unified_ws.router, tags=["unified-ws"])""",
)

with io.open(path, "w", encoding="utf-8", newline="") as f:
    f.write(src)
py_compile.compile(path, doraise=True)
print("api/main.py OK, lines:", src.count(chr(10)))
