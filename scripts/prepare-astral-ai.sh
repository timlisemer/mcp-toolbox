#!/usr/bin/env bash
# Run as a separate provisioning step with the same mounts and user as the service.
set -euo pipefail
binary=/app/tools/astral-ai/bin/astral-ai
"$binary" hosting select docker --if-unset \
    --control-dir /var/lib/astral-ai/instances/docker/control
"$binary" account bootstrap --if-unset --display-name "Local user" \
    --data-dir /var/lib/astral-ai/data
