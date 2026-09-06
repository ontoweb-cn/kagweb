# ============================================
# DeepMentor Multi-Stage Dockerfile
# ============================================
# This Dockerfile builds a production-ready image for DeepMentor
# containing both the FastAPI backend and Next.js frontend
#
# Build/run:
#   docker build -t deepmentor:local .
#   docker run -p 127.0.0.1:3782:3782 -p 127.0.0.1:8001:8001 \
#     -v deepmentor-data:/app/data deepmentor:local
#
# Prerequisites:
#   1. Runtime settings are created under data/user/settings on first start
#   2. Configure provider profiles from the web Settings page or model_catalog.json
# ============================================

# ============================================
# Stage 1: Frontend Builder
# ============================================
# Builds natively on the host architecture — no cross-arch builds:
# each machine packages a container for its own platform only.
FROM node:22-slim AS frontend-builder

WORKDIR /app/web

# Copy package files first for better caching
COPY web/package.json web/package-lock.json* ./

# Install dependencies with generous timeout for CI environments
RUN npm config set fetch-timeout 600000 && \
    npm config set fetch-retries 5 && \
    npm ci --legacy-peer-deps

# Copy frontend source code
COPY web/ ./

# Subpath deployments (e.g. https://ai.wust.edu.cn/deepmentor): pass
# --build-arg NEXT_PUBLIC_BASE_PATH=/deepmentor. It is baked at build time
# (Next basePath + the browser-side URL chokepoints in web/shared/base-path.ts);
# changing it requires a rebuild.
ARG NEXT_PUBLIC_BASE_PATH=""

# Provide the single source of truth for the app version so next.config.js
# can read it during ``npm run build`` and inline it into the bundle.
COPY deepmentor/__version__.py /app/deepmentor/__version__.py

# Create .env.local with the single env var the build needs (the app version,
# exposed to the browser via next.config.js). URL knowledge is no longer baked
# into the bundle: `apiUrl`/`wsUrl` in web/lib/api.ts are pass-throughs and
# the actual backend host is read at request time by web/proxy.ts from
# DEEPMENTOR_API_BASE_URL (exported by the entrypoint on every start).
RUN printf 'NEXT_PUBLIC_APP_VERSION=\n' > .env.local

# Build Next.js for production with standalone output
# This allows runtime environment variable injection
RUN npm run build

# ============================================
# Stage 2: Python Base with Dependencies
# ============================================
FROM python:3.11-slim AS python-base

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install system dependencies
# Note: libgl1 and libglib2.0-0 are required for OpenCV (used by mineru)
# Rust is required for building tiktoken and other packages without pre-built wheels
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    git \
    build-essential \
    libgl1 \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender1 \
    pkg-config \
    libssl-dev \
    && rm -rf /var/lib/apt/lists/* \
    && curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y

# Add Rust to PATH
ENV PATH="/root/.cargo/bin:${PATH}"

# Copy requirements and install Python dependencies
COPY requirements/ ./requirements/
COPY requirements.txt ./
RUN pip install --upgrade pip && \
    pip install -r requirements.txt

# ============================================
# Stage 3: Production Image
# ============================================
FROM python:3.11-slim AS production

# Labels
LABEL maintainer="DeepMentor Team" \
      description="DeepMentor: AI-Powered Personalized Learning Assistant"

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    MALLOC_ARENA_MAX=2 \
    MALLOC_TRIM_THRESHOLD_=131072 \
    NODE_ENV=production \
    DEEPMENTOR_IGNORE_PROCESS_ENV_OVERRIDES=1

# Code-execution sandbox: the restricted-subprocess backend (which the office
# skills — docx/pdf/pptx/xlsx — rely on for `exec` / `code_execution`) is
# enabled by default via the `sandbox_allow_subprocess` runtime setting
# (system.json, default on), exported to DEEPMENTOR_SANDBOX_ALLOW_SUBPROCESS at
# startup. No hardcoded ENV here — that would override the setting and block
# disabling it. docker-compose still routes exec to the hardened runner sidecar
# (DEEPMENTOR_SANDBOX_RUNNER_URL), which build_backend() prefers.

WORKDIR /app

# Install system dependencies
# Note: libgl1 and libglib2.0-0 are required for OpenCV (used by mineru)
# Note: git is required to install CLI apps — most of the CLI-Anything catalog
#       installs with `pip install git+…`, which shells out to git. It is needed
#       in *this* image and not in the runner: installing is a privileged
#       main-app action, running is the runner's (Dockerfile.runner).
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    bash \
    git \
    supervisor \
    libgl1 \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender1 \
    && rm -rf /var/lib/apt/lists/*

# Copy Node.js from the frontend-builder stage (same node:22-slim base,
# so the runtime matches the build host architecture — no cross-arch builds)
COPY --from=frontend-builder /usr/local/bin/node /usr/local/bin/node
COPY --from=frontend-builder /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -sf /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -sf /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
    && node --version && npm --version

# Copy Python packages from builder stage
COPY --from=python-base /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=python-base /usr/local/bin /usr/local/bin

# Copy built frontend from frontend-builder stage (standalone mode)
# The standalone output contains a self-contained server.js + minimal node_modules
# Static assets and public/ must be copied alongside standalone manually
COPY --from=frontend-builder /app/web/.next/standalone/ ./web/
COPY --from=frontend-builder /app/web/.next/static/ ./web/.next/static/
COPY --from=frontend-builder /app/web/public/ ./web/public/

# Copy application source code
COPY deepmentor/ ./deepmentor/
COPY deepmentor_cli/ ./deepmentor_cli/
COPY scripts/ ./scripts/
COPY pyproject.toml ./
COPY requirements/ ./requirements/
COPY requirements.txt ./

# Ship the license and third-party notices with the image: distributed
# containers carry the Apache-2.0 text and the adapted/vendored component
# notices (CSSwitch, Hermes Agent, vendored thinking-orbs under web/).
COPY LICENSE THIRD_PARTY_NOTICES.md ./

# Create necessary directories (these will be overwritten by volume mounts)
RUN mkdir -p \
    data/user/settings \
    data/memory \
    data/user/workspace/memory \
    data/user/workspace/notebook \
    data/user/workspace/co-writer/audio \
    data/user/workspace/co-writer/tool_calls \
    data/user/workspace/chat/chat \
    data/user/workspace/chat/deep_solve \
    data/user/workspace/chat/deep_question \
    data/user/workspace/chat/deep_research/reports \
    data/user/workspace/chat/math_animator \
    data/user/workspace/chat/_detached_code_execution \
    data/user/logs \
    data/knowledge_bases

# Bake a non-root user (UID 1000) for the supervisord programs. supervisord
# itself runs as PID 1's UID — root under rootful Docker/Podman, or UID 1000
# under rootless podman + `userns_mode: keep-id` (where PID 1 is the host
# user). Each child (backend/frontend) is dropped to this `deepmentor` user via
# the per-program `user=deepmentor` directive, so the app processes stay
# non-root in either runtime. UID 1000 also matches the host user under
# keep-id with a bind mount on ./data.
RUN groupadd --system --gid 1000 deepmentor \
    && useradd --system --uid 1000 --gid 1000 --no-create-home --shell /usr/sbin/nologin deepmentor \
    && chown -R deepmentor:deepmentor /app/data /app/web/.next

# supervisord config is split into two files so the production and development
# images share one daemon-level [supervisord] section instead of duplicating it:
#   - /etc/supervisor/supervisord.conf      — daemon-level settings (shared)
#   - /etc/supervisor/conf.d/programs.conf  — the backend/frontend programs
# Production defines the programs here; the development stage overrides only
# programs.conf, leaving the shared daemon section untouched.
# Program output goes to the container's stdout/stderr so `docker logs` captures it.
RUN mkdir -p /etc/supervisor/conf.d

# Daemon-level config. No `user=` in [supervisord]: supervisord runs as PID 1's
# UID (root under rootful; UID 1000 under rootless podman + keep-id, which has
# no CAP_SETUID and would make a `user=` line fail at startup with
# "Can't drop privilege as nonroot user" — see supervisord options.py). The
# pidfile lives in /tmp, which is world-writable, so supervisord can create it
# whether it runs as root or UID 1000; /var/run is root-owned and not writable
# by UID 1000 under rootless keep-id.
RUN cat > /etc/supervisor/supervisord.conf <<'EOF'
[supervisord]
nodaemon=true
logfile=/dev/null
logfile_maxbytes=0
pidfile=/tmp/supervisord.pid

[include]
files = /etc/supervisor/conf.d/programs.conf
EOF

RUN sed -i 's/\r$//' /etc/supervisor/supervisord.conf

# Program definitions (production). Each child drops to the unprivileged
# deepmentor user (UID 1000) via per-program `user=deepmentor`; see the note on
# the user= design above the daemon config.
RUN cat > /etc/supervisor/conf.d/programs.conf <<'EOF'
[program:backend]
command=/bin/bash /app/start-backend.sh
directory=/app
user=deepmentor
autostart=true
autorestart=true
stdout_logfile=/dev/fd/1
stdout_logfile_maxbytes=0
stderr_logfile=/dev/fd/2
stderr_logfile_maxbytes=0
environment=PYTHONPATH="/app",PYTHONUNBUFFERED="1"

[program:frontend]
command=/bin/bash /app/start-frontend.sh
directory=/app/web
user=deepmentor
autostart=true
autorestart=true
startsecs=5
stdout_logfile=/dev/fd/1
stdout_logfile_maxbytes=0
stderr_logfile=/dev/fd/2
stderr_logfile_maxbytes=0
environment=NODE_ENV="production"
EOF

RUN sed -i 's/\r$//' /etc/supervisor/conf.d/programs.conf

# Per-component program files for split deployments (Kubernetes, or any
# orchestrator that wants one concern per container). The entrypoint copies
# the selected file over programs.conf when DEEPMENTOR_COMPONENT is set to
# backend / frontend; supervisord itself only ever reads conf.d/programs.conf
# (the include line above names programs.conf only). Both files are SLICED
# out of programs.conf at build time so the three can never drift — editing
# a program in programs.conf is the single source of truth.
RUN sed -n '/^\[program:backend\]/,/^$/p' /etc/supervisor/conf.d/programs.conf > /etc/supervisor/conf.d/programs-backend.conf \
    && sed -n '/^\[program:frontend\]/,$p' /etc/supervisor/conf.d/programs.conf > /etc/supervisor/conf.d/programs-frontend.conf

# The entrypoint (which runs as PID 1's UID — UID 1000 under Kubernetes
# runAsUser: 1000) rewrites programs.conf when DEEPMENTOR_COMPONENT selects a
# split deployment, so the selectable files must be owned by that user.
RUN chown deepmentor:deepmentor /etc/supervisor/conf.d \
    /etc/supervisor/conf.d/programs.conf \
    /etc/supervisor/conf.d/programs-backend.conf \
    /etc/supervisor/conf.d/programs-frontend.conf

# Create backend startup script
RUN cat > /app/start-backend.sh <<'EOF'
#!/bin/bash
set -e

BACKEND_PORT=${BACKEND_PORT:-8001}
BACKEND_HOST=${BACKEND_HOST:-0.0.0.0}
BACKEND_WORKERS=${BACKEND_WORKERS:-1}

echo "[Backend]  🚀 Starting FastAPI backend on ${BACKEND_HOST}:${BACKEND_PORT}..."

# Run uvicorn directly - the application's logging system already handles:
# 1. Console output (visible in docker logs)
# 2. File logging to data/user/logs/ai_tutor_*.log
#
# BACKEND_HOST defaults to 0.0.0.0 (LAN-reachable, matches bridge-mode
# port publishing). Set BACKEND_HOST=127.0.0.1 when running with
# network_mode: host to keep the backend on loopback only.
#
# --ws-max-size: chat attachments travel base64 inside one WS message; derive
# the frame cap from the configured attachment policy (system.json) so uploads
# the policy allows are not severed by uvicorn's 16MB default.
#
# --timeout-keep-alive: the frontend proxy (web/proxy.ts) forwards over Node's
# http.globalAgent, which reaps idle sockets on a 5s timer — identical to
# uvicorn's default, so both ends raced to close the same socket and the loser's
# request died with ECONNRESET (a 500 in the UI). Stay well above the proxy's
# reaper so the client is the only side retiring idle connections.
WS_MAX_SIZE=$(python -c "from deepmentor.services.config import get_ws_max_size; print(get_ws_max_size())" 2>/dev/null || echo 16777216)
KEEP_ALIVE=$(python -c "from deepmentor.services.config import HTTP_KEEP_ALIVE_TIMEOUT; print(HTTP_KEEP_ALIVE_TIMEOUT)" 2>/dev/null || echo 300)
exec python -m uvicorn deepmentor.api.main:app --host ${BACKEND_HOST} --port ${BACKEND_PORT} --workers ${BACKEND_WORKERS} --no-access-log --ws-max-size ${WS_MAX_SIZE} --timeout-keep-alive ${KEEP_ALIVE}
EOF

RUN sed -i 's/\r$//' /app/start-backend.sh && chmod +x /app/start-backend.sh

# Create frontend startup script
# This script starts the Next.js standalone server. URL knowledge is no
# longer baked into the bundle: web/proxy.ts rewrites /api/* and /ws/* to
# the configured backend at request time, reading DEEPMENTOR_API_BASE_URL
# (exported by the entrypoint from data/user/settings/system.json).
RUN cat > /app/start-frontend.sh <<'EOF'
#!/bin/bash
set -e

FRONTEND_PORT=${FRONTEND_PORT:-3782}
FRONTEND_HOST=${FRONTEND_HOST:-0.0.0.0}
echo "[Frontend] 🚀 Starting Next.js frontend on ${FRONTEND_HOST}:${FRONTEND_PORT}..."

export PORT=${FRONTEND_PORT}
export HOSTNAME=${FRONTEND_HOST}
exec node /app/web/server.js
EOF

RUN sed -i 's/\r$//' /app/start-frontend.sh && chmod +x /app/start-frontend.sh

# Create entrypoint script
RUN cat > /app/entrypoint.sh <<'EOF'
#!/bin/bash
set -e

echo "============================================"
echo "🚀 Starting DeepMentor"
echo "============================================"

export DEEPMENTOR_IGNORE_PROCESS_ENV_OVERRIDES=1

# Docker is JSON-driven. Ignore runtime env names even if the host or a stale
# Compose environment provides them; the entrypoint re-exports values from
# data/user/settings/*.json below.
for key in \
    BACKEND_PORT \
    BACKEND_WORKERS \
    DEEPMENTOR_BACKEND_WORKERS \
    FRONTEND_PORT \
    NEXT_PUBLIC_API_BASE_EXTERNAL \
    NEXT_PUBLIC_API_BASE \
    CORS_ORIGIN \
    CORS_ORIGINS \
    DISABLE_SSL_VERIFY \
    CHAT_ATTACHMENT_DIR \
    AUTH_ENABLED \
    NEXT_PUBLIC_AUTH_ENABLED \
    AUTH_USERNAME \
    AUTH_PASSWORD_HASH \
    AUTH_TOKEN_EXPIRE_HOURS \
    AUTH_COOKIE_SECURE \
    POCKETBASE_URL \
    POCKETBASE_PORT \
    POCKETBASE_EXTERNAL_URL \
    POCKETBASE_ADMIN_EMAIL \
    POCKETBASE_ADMIN_PASSWORD \
    DEEPMENTOR_API_BASE_URL \
    DEEPMENTOR_AUTH_ENABLED; do
    unset "$key"
done

# Initialize user data directories if empty
echo "📁 Checking data directories..."
echo "   Ensuring runtime settings and workspace layout..."
python -c "
from pathlib import Path
from deepmentor.services.setup import init_user_directories
init_user_directories(Path('/app'))
" 2>/dev/null || echo "   ⚠️ Directory initialization skipped (will be created on first use)"

# Idempotent ownership fix for the mounted data volume. The recursive chown
# only runs when /app/data's owner actually differs from the deepmentor user
# AND we can act on it (running as root): a volume provisioned for UID 1000
# (or mounted with Kubernetes fsGroup: 1000) then costs one stat per start
# instead of a full tree walk, and non-root entrypoints skip it quietly.
if [ "$(id -u)" = "0" ] && [ "$(stat -c %u /app/data 2>/dev/null || echo 0)" != "$(id -u deepmentor)" ]; then
    chown -R deepmentor:deepmentor /app/data 2>/dev/null || true
fi

# Optional dependencies (#762). A container is disposable, so anything
# `docker exec … pip install`ed into a running one is gone at the next
# `compose down`. Declare them on the deployment instead and every container
# started from it has them:
#
#   environment:
#     DEEPMENTOR_EXTRAS: "math-animator,partners"
#     DEEPMENTOR_APT_PACKAGES: "ffmpeg"
#
# Both steps are idempotent — a warm container only pays a check — and neither
# is allowed to be fatal: a missing wheel leaves that one feature unavailable,
# exactly as it was before, rather than taking the whole deployment down.
# The pip cache lives on the data volume so a rebuild reuses the downloads it
# already paid for instead of fetching them again.
export PIP_CACHE_DIR="${PIP_CACHE_DIR:-/app/data/.cache/pip}"
mkdir -p "$PIP_CACHE_DIR" 2>/dev/null || true

if [ -n "${DEEPMENTOR_APT_PACKAGES:-}" ]; then
    echo "🔧 Ensuring system packages: ${DEEPMENTOR_APT_PACKAGES}"
    apt_missing=""
    for pkg in $(echo "${DEEPMENTOR_APT_PACKAGES}" | tr ',' ' '); do
        dpkg -s "$pkg" >/dev/null 2>&1 || apt_missing="$apt_missing $pkg"
    done
    if [ -z "$apt_missing" ]; then
        echo "   ✅ System packages already present"
    elif ! (apt-get update -qq && apt-get install -y --no-install-recommends $apt_missing); then
        echo "   ⚠️ apt-get failed; these packages stay unavailable:$apt_missing"
    fi
fi

if [ -n "${DEEPMENTOR_EXTRAS:-}" ]; then
    echo "🔧 Ensuring Python extras: ${DEEPMENTOR_EXTRAS}"
    python /app/scripts/install_extras.py "${DEEPMENTOR_EXTRAS}" || true
    chown -R deepmentor:deepmentor "$PIP_CACHE_DIR" 2>/dev/null || true
fi

echo "⚙️  Loading runtime JSON settings..."
eval "$(python - <<'PY'
import shlex
from deepmentor.services.config import export_runtime_settings_to_env

for key, value in export_runtime_settings_to_env(overwrite=True).items():
    print(f"export {key}={shlex.quote(str(value))}")
PY
)"

export BACKEND_PORT=${BACKEND_PORT:-8001}
export FRONTEND_PORT=${FRONTEND_PORT:-3782}

# DEEPMENTOR_API_BASE_URL and DEEPMENTOR_AUTH_ENABLED are exported by the
# export_runtime_settings_to_env eval above (see render_environment in
# deepmentor/services/config/runtime_settings.py). web/proxy.ts reads them at
# request time to rewrite /api/* and /ws/* to the backend and to gate the login
# redirect. Keeping them in the single JSON-backed exporter means the Docker and
# `deepmentor start` paths stay in sync.
echo "📌 API Base URL (proxy): ${DEEPMENTOR_API_BASE_URL:-http://localhost:${BACKEND_PORT}}"
echo "📌 Auth enabled: ${DEEPMENTOR_AUTH_ENABLED:-false}"

echo "📌 Backend Port: ${BACKEND_PORT}"
echo "📌 Frontend Port: ${FRONTEND_PORT}"

echo "============================================"
echo "📦 Configuration loaded from:"
echo "   - data/user/settings/system.json"
echo "   - data/user/settings/auth.json"
echo "   - data/user/settings/integrations.json"
echo "   - data/user/settings/model_catalog.json"
echo "   - data/user/settings/main.yaml"
echo "   - data/user/settings/agents.yaml"
echo "============================================"

# Split-deployment switch: DEEPMENTOR_COMPONENT selects which supervisord
# programs this container runs (all = both, the default). Kubernetes — or any
# orchestrator that wants one concern per container — runs this single image
# with DEEPMENTOR_COMPONENT=backend / frontend and gets a single-program
# container without a second image or a forked entrypoint, so settings
# loading, extras installation and the env re-export below stay identical in
# every shape. Note: the image-level HEALTHCHECK keeps probing the backend
# port; orchestrators define per-container probes, but plain `docker ps`
# will show "unhealthy" for a frontend-only container.
COMPONENT=${DEEPMENTOR_COMPONENT:-all}
case "${COMPONENT}" in
    all) : ;;
    backend|frontend)
        cp "/etc/supervisor/conf.d/programs-${COMPONENT}.conf" \
           /etc/supervisor/conf.d/programs.conf
        echo "🧩 Component mode: ${COMPONENT}"
        ;;
    *)
        echo "ERROR: DEEPMENTOR_COMPONENT must be all, backend or frontend (got: ${COMPONENT})" >&2
        exit 1
        ;;
esac

# Hand off to supervisord as PID 1. The daemon-level config deliberately omits
# `user=` so supervisord inherits PID 1's UID and stays portable across rootful
# and rootless-keep-id runtimes; children drop to the deepmentor user via
# per-program `user=`. Full rationale lives next to the [supervisord] section
# in the build step that writes /etc/supervisor/supervisord.conf.
exec /usr/bin/supervisord -c /etc/supervisor/supervisord.conf
EOF

RUN sed -i 's/\r$//' /app/entrypoint.sh && chmod +x /app/entrypoint.sh

RUN cat > /app/healthcheck.py <<'EOF'
from pathlib import Path
import json
import urllib.request

port = 8001
settings_path = Path("/app/data/user/settings/system.json")
try:
    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    port = int(settings.get("backend_port") or port)
except Exception:
    pass

urllib.request.urlopen(f"http://localhost:{port}/health/ready", timeout=5).close()
EOF

# Expose ports
EXPOSE 8001 3782

# Health check. Read the port from JSON so standalone `docker run` does not
# depend on a Dockerfile-level BACKEND_PORT default.
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD python /app/healthcheck.py

# Set entrypoint
ENTRYPOINT ["/app/entrypoint.sh"]

# ============================================
# Stage 4: Development Image (Optional)
# ============================================
FROM production AS development

# `next dev` compiles from source, so the development image needs the whole
# web tree. This used to cherry-pick node_modules, package.json and
# next.config.js on top of the production stage — but that stage's ./web is
# `.next/standalone/`, a compiled server bundle carrying no sources, no
# tsconfig and no scripts/. So the supervisor program below launched
# `node scripts/dev.mjs` against a path that never existed, the dev frontend
# went FATAL, and the image looked broken (#906). Taking the builder's tree
# wholesale also stops the list from drifting each time web/ grows a
# top-level entry. `--chown` during the copy avoids re-layering node_modules.
COPY --chown=deepmentor:deepmentor --from=frontend-builder /app/web ./web

# `next dev` runs as the unprivileged deepmentor user (via `user=deepmentor` in
# the supervisord config) and must create/write its build cache under
# /app/web/.next, so give that user ownership of the web dir and the cache.
# The production build copied in above is not reusable by `next dev`, so it
# starts from an empty cache rather than a half-valid one.
RUN rm -rf /app/web/.next \
    && mkdir -p /app/web/.next \
    && chown deepmentor:deepmentor /app/web /app/web/.next

# Install development tools
RUN apt-get update && apt-get install -y --no-install-recommends \
    vim \
    git \
    && rm -rf /var/lib/apt/lists/*

# Install development Python packages
RUN pip install --no-cache-dir \
    pre-commit \
    black \
    ruff

# Development overrides only the program definitions (uvicorn --reload and
# `next dev`); the shared daemon-level /etc/supervisor/supervisord.conf from
# the production stage is reused as-is.
RUN cat > /etc/supervisor/conf.d/programs.conf <<'EOF'
[program:backend]
command=/bin/bash -c "exec python -m uvicorn deepmentor.api.main:app --host 0.0.0.0 --port ${BACKEND_PORT:-8001} --reload --no-access-log --ws-max-size $(python -c 'from deepmentor.services.config import get_ws_max_size; print(get_ws_max_size())' 2>/dev/null || echo 16777216) --timeout-keep-alive $(python -c 'from deepmentor.services.config import HTTP_KEEP_ALIVE_TIMEOUT; print(HTTP_KEEP_ALIVE_TIMEOUT)' 2>/dev/null || echo 300)"
directory=/app
user=deepmentor
autostart=true
autorestart=true
stdout_logfile=/dev/fd/1
stdout_logfile_maxbytes=0
stderr_logfile=/dev/fd/2
stderr_logfile_maxbytes=0
environment=PYTHONPATH="/app",PYTHONUNBUFFERED="1"

[program:frontend]
command=/bin/bash -c "cd /app/web && node scripts/dev.mjs -H 0.0.0.0 -p ${FRONTEND_PORT:-3782}"
directory=/app/web
user=deepmentor
autostart=true
autorestart=true
startsecs=5
stdout_logfile=/dev/fd/1
stdout_logfile_maxbytes=0
stderr_logfile=/dev/fd/2
stderr_logfile_maxbytes=0
environment=NODE_ENV="development"
EOF

RUN sed -i 's/\r$//' /etc/supervisor/conf.d/programs.conf

# Development ports
EXPOSE 8001 3782
