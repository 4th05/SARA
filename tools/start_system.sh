#!/bin/sh
set -eu
project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$project_dir"
export LANGSMITH_TRACING=false
export MPLCONFIGDIR="$project_dir/.local/matplotlib"
export HF_HOME="$project_dir/.local/models/huggingface"
exec "$project_dir/.venv/bin/python" -m gateway.run "$@"
