#!/usr/bin/env bash
set -euo pipefail
if ! jq -e '.tools["astral-ai"].enabled == true' /app/config/servers.json >/dev/null; then
    exit 0
fi
exec /app/tools/astral-ai/bin/astral-ai health --data-dir /var/lib/astral-ai/data
