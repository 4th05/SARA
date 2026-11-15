#!/bin/sh
set -eu
project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$project_dir"
if [ ! -f .env ]; then
    cp .env.example .env
    chmod 600 .env
fi
if command -v brew >/dev/null 2>&1; then
    node_prefix=$(brew --prefix node@24 2>/dev/null || true)
    if [ -x "$node_prefix/bin/node" ]; then
        PATH="$node_prefix/bin:$PATH"
        export PATH
    fi
fi
if [ ! -x .venv/bin/python ]; then
    python_command=python3
    if command -v python3.12 >/dev/null 2>&1; then
        python_command=python3.12
    fi
    "$python_command" -m venv .venv
fi
.venv/bin/python -m pip install -r gateway/requirements.txt
npm --prefix orchestration/node-red ci
if [ "${1:-}" = "--with-gesture" ]; then
    .venv/bin/python -m pip install -r gateway/requirements-gesture.txt
fi
exec .venv/bin/python -m gateway.download_models "$@"
