#!/bin/bash

# Double-click launcher for KAGWeb on macOS.
# Keep the terminal open while KAGWeb is running so Ctrl+C stops both
# the backend and frontend managed by `kagweb start`.

set -u

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR" || exit 1

# Finder-launched .command files may not inherit the usual Homebrew paths.
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

PYTHON=""
if [ -x "$PROJECT_DIR/.venv/bin/python" ]; then
    PYTHON="$PROJECT_DIR/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
    PYTHON="$(command -v python3)"
fi

echo "Starting KAGWeb..."
echo "Workspace: $PROJECT_DIR"

if [ -n "$PYTHON" ] && "$PYTHON" -c 'import kagweb_cli.main' >/dev/null 2>&1; then
    exec "$PYTHON" -m kagweb_cli.main start --home "$PROJECT_DIR" "$@"
fi

if command -v kagweb >/dev/null 2>&1; then
    exec "$(command -v kagweb)" start --home "$PROJECT_DIR" "$@"
fi

if [ -n "$PYTHON" ]; then
    echo "Error: KAGWeb Python dependencies are not installed for: $PYTHON"
    echo "Run this once, then double-click this file again:"
    echo "  \"$PYTHON\" -m pip install -e \"$PROJECT_DIR\""
    echo "  (cd \"$PROJECT_DIR/web\" && npm ci --legacy-peer-deps)"
    exit 1
fi

echo "Error: Python 3 or the 'kagweb' command was not found."
echo "Install KAGWeb and its dependencies, then double-click this file again."
echo "See README.md for installation instructions."
exit 1
