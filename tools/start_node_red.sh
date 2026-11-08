#!/bin/sh
set -eu

project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)

# Prefer Homebrew's Node.js 24 on this Mac. Ubuntu uses its installed Node.js.
if command -v brew >/dev/null 2>&1; then
    node_prefix=$(brew --prefix node@24)
    if [ -x "$node_prefix/bin/node" ]; then
        PATH="$node_prefix/bin:$PATH"
        export PATH
    fi
fi

node_major=$(node -p 'process.versions.node.split(".")[0]')
if [ "$node_major" != "24" ]; then
    printf '%s\n' "This project uses Node.js 24. Install or select it before starting Node-RED." >&2
    exit 1
fi

exec npm --prefix "$project_dir/orchestration/node-red" start
