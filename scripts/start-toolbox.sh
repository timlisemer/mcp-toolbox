#!/usr/bin/env bash

if ! jq -e '.tools["astral-ai"].enabled == true' /app/config/servers.json >/dev/null; then
    exec /run/current-system/sw/bin/tail -f /dev/null
fi

/app/tools/astral-ai/bin/astral-ai serve \
    --mode docker \
    --control-dir /var/lib/astral-ai/control \
    --data-dir /var/lib/astral-ai/data \
    --socket /var/lib/astral-ai/data/service.sock &
ai_pid=$!

shutdown() {
    trap '' TERM INT
    kill -TERM "$ai_pid" 2>/dev/null || true
    wait "$ai_pid" 2>/dev/null || true
    exit 0
}
trap shutdown TERM INT

wait "$ai_pid"
status=$?
echo "Astral AI service exited (status $status). The toolbox remains available." >&2
exec /run/current-system/sw/bin/tail -f /dev/null
