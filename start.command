#!/bin/zsh
set -e
PROJECT_DIR="${0:A:h}"
cd "$PROJECT_DIR"
PYTHON_BIN="${SIMPLE_SYNC_PYTHON:-$PROJECT_DIR/../env/sync-env/bin/python}"
if [[ ! -x "$PYTHON_BIN" ]]; then
    PYTHON_BIN="$(command -v python3)"
fi
exec "$PYTHON_BIN" -m sync_server
