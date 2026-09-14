#!/usr/bin/env bash
set -euo pipefail

if ! jq -e '.tools["astral-ai"].enabled == true' /app/config/servers.json >/dev/null; then
    exec /run/current-system/sw/bin/tail -f /dev/null
fi

# PID 1 is the service. A startup failure stops the container and is visible to its supervisor.
exec /app/tools/astral-ai/bin/astral-ai serve \
    --settings /app/config/astral-settings.toml \
    --mode docker \
    --control-dir /var/lib/astral-ai/instances/docker/control \
    --data-dir /var/lib/astral-ai/data \
    --socket /var/lib/astral-ai/data/service.sock
