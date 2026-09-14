# syntax=docker/dockerfile:1
# MCP Toolbox - Pre-builds MCP tools for on-demand invocation
ARG NIXOS_CI_IMAGE=ghcr.io/timlisemer/nixos-ci:latest
FROM ${NIXOS_CI_IMAGE}

SHELL ["/run/current-system/sw/bin/bash", "-c"]

ENV GOPATH="/root/go"

# Create directory structure
WORKDIR /app
RUN mkdir -p /app/tools /app/config /app/bin

# Copy configuration and build scripts
COPY config/ /app/config/
COPY patches/ /app/patches/
COPY scripts/install.sh scripts/start-toolbox.sh scripts/prepare-astral-ai.sh scripts/health-toolbox.sh /app/scripts/
COPY bridge/mcp_path_bridge.py /app/bin/mcp-path-bridge
RUN chmod +x /app/scripts/*.sh /app/bin/mcp-path-bridge

# Pre-build all MCP tools. The optional BuildKit secret provides read-only
# access to private repositories without persisting credentials in a layer.
RUN --mount=type=secret,id=github_token \
    --mount=type=cache,id=mcp-toolbox-cargo-git,target=/root/.cargo/git,sharing=locked \
    --mount=type=cache,id=mcp-toolbox-cargo-registry,target=/root/.cargo/registry,sharing=locked \
    --mount=type=cache,id=mcp-toolbox-cargo-target,target=/app/cargo-target,sharing=locked \
    CARGO_TARGET_DIR=/app/cargo-target /run/current-system/sw/bin/bash /app/scripts/install.sh

# Service-owned provider subprocesses run inside this image, not on the host.
RUN if jq -e '.tools["astral-ai"].enabled == true' /app/config/servers.json >/dev/null; then \
      npm install --global --prefix /app/providers @openai/codex @anthropic-ai/claude-code && \
      test -x /app/providers/bin/codex && test -x /app/providers/bin/claude; \
    fi
ENV PATH="/app/providers/bin:/run/current-system/sw/bin"

# The health probe performs an authenticated service handshake.
HEALTHCHECK --interval=15s --timeout=10s --start-period=60s --retries=3 CMD ["/run/current-system/sw/bin/bash", "/app/scripts/health-toolbox.sh"]

# Start the optional AI service.
CMD ["/run/current-system/sw/bin/bash", "/app/scripts/start-toolbox.sh"]
