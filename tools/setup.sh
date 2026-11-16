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
for program in node npm ollama; do
    if ! command -v "$program" >/dev/null 2>&1; then
        printf '%s\n' "Missing $program. Follow the Linux installation steps in README.md." >&2
        exit 1
    fi
done
"$project_dir/tools/start_node_red.sh" --check
if command -v systemctl >/dev/null 2>&1 && systemctl is-active --quiet ollama.service 2>/dev/null; then
    printf '%s\n' "Stop Ollama's system service first: sudo systemctl disable --now ollama" >&2
    printf '%s\n' "SARA manages its own Ollama process and project-local model files." >&2
    exit 1
fi
if [ -x .venv/bin/python ]; then
    python_command=.venv/bin/python
elif command -v python3.12 >/dev/null 2>&1; then
    python_command=python3.12
elif command -v python3 >/dev/null 2>&1; then
    python_command=python3
else
    printf '%s\n' "Python 3.12 is required. Follow README.md." >&2
    exit 1
fi
"$python_command" - <<'CHECK_PYTHON'
import sys
if sys.version_info[:2] != (3, 12):
    raise SystemExit("Python 3.12 is required. Follow README.md or recreate .venv with Python 3.12.")
CHECK_PYTHON
if [ ! -x .venv/bin/python ]; then
    "$python_command" -m venv .venv
fi
.venv/bin/python -m pip install --upgrade 'pip>=26.2'
.venv/bin/python -m pip install -r gateway/requirements.txt
npm --prefix orchestration/node-red ci --engine-strict
exec .venv/bin/python -m gateway.download_models "$@"
