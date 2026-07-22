# syntax=docker/dockerfile:1
# MCP Toolbox - Pre-builds MCP tools for on-demand invocation
# agent-framework-rs requires Rust 1.95; Debian Trixie's rustc is only 1.85.
FROM rust:1.95-slim-trixie

# Install runtimes and build tools from Debian LTS-supported repositories.
ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    bash \
    clang \
    cmake \
    curl \
    git \
    build-essential \
    ca-certificates \
    golang-go \
    jq \
    libclang-dev \
    libssl-dev \
    nodejs \
    npm \
    pkg-config \
    python3 \
    python3-pip \
    python3-venv \
    && rm -rf /var/lib/apt/lists/*
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
RUN --mount=type=secret,id=github_token /app/scripts/install.sh

# Stay alive for docker exec access - tools are invoked on-demand
CMD ["tail", "-f", "/dev/null"]
