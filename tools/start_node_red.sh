#!/bin/sh
set -eu

project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)

# Prefer Homebrew's Node.js 24 on this Mac. Ubuntu uses its installed Node.js.
if command -v brew >/dev/null 2>&1; then
    node_prefix=$(brew --prefix node@24 2>/dev/null || true)
    if [ -x "$node_prefix/bin/node" ]; then
        PATH="$node_prefix/bin:$PATH"
        export PATH
    fi
fi

node_supported=$(node -p 'const [major, minor] = process.versions.node.split(".").map(Number); major === 24 && minor >= 15')
if [ "$node_supported" != "true" ]; then
    printf '%s\n' "This project requires Node.js 24.15 or newer within version 24." >&2
    exit 1
fi

if [ "${1:-}" = "--check" ]; then
    exit 0
fi

exec node "$project_dir/orchestration/node-red/node_modules/node-red/red.js" \
    --settings "$project_dir/orchestration/node-red/settings.js"
