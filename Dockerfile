# syntax=docker/dockerfile:1
# MCP Toolbox - Pre-builds MCP tools for on-demand invocation
ARG NIXOS_CI_IMAGE=ghcr.io/timlisemer/nixos-ci:latest
FROM ${NIXOS_CI_IMAGE}

SHELL ["/run/current-system/sw/bin/bash", "-c"]

ENV GOPATH="/root/go"
ENV PLAYWRIGHT_BROWSERS_PATH="/ms-playwright"

# Create directory structure
WORKDIR /app
RUN mkdir -p /app/tools /app/config

# Copy configuration and build scripts
COPY config/ /app/config/
COPY patches/ /app/patches/
COPY scripts/install.sh /app/scripts/
RUN chmod +x /app/scripts/*.sh

# Pre-build all MCP tools. The optional BuildKit secret provides read-only
# access to private repositories without persisting credentials in a layer.
RUN --mount=type=secret,id=github_token \
    --mount=type=cache,id=mcp-toolbox-cargo-git,target=/root/.cargo/git,sharing=locked \
    --mount=type=cache,id=mcp-toolbox-cargo-registry,target=/root/.cargo/registry,sharing=locked \
    --mount=type=cache,id=mcp-toolbox-cargo-target,target=/app/cargo-target,sharing=locked \
    CARGO_TARGET_DIR=/app/cargo-target /run/current-system/sw/bin/bash /app/scripts/install.sh

# Stay alive for docker exec access - tools are invoked on-demand
CMD ["/run/current-system/sw/bin/tail", "-f", "/dev/null"]
