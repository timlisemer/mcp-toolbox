# MCP Toolbox - Pre-builds MCP tools for on-demand invocation
FROM debian:trixie-slim

# Install runtimes and build tools from Debian LTS-supported repositories.
ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    bash \
    curl \
    git \
    build-essential \
    ca-certificates \
    cargo \
    golang-go \
    jq \
    just \
    nodejs \
    npm \
    python3 \
    python3-pip \
    python3-venv \
    rust-analyzer \
    rust-src \
    rustc \
    && rm -rf /var/lib/apt/lists/*
ENV GOPATH="/root/go"

# Create directory structure
WORKDIR /app
RUN mkdir -p /app/tools /app/tools-builtin /app/servers /app/config

# Copy configuration and build scripts
COPY config/ /app/config/
COPY scripts/install.sh /app/scripts/
COPY scripts/entrypoint.sh /app/scripts/
RUN chmod +x /app/scripts/*.sh

# Pre-build all MCP tools
RUN /app/scripts/install.sh

# Declare volume for persistent server data
VOLUME /app/servers

# Use entrypoint for runtime initialization
ENTRYPOINT ["/app/scripts/entrypoint.sh"]

# Stay alive for docker exec access - tools are invoked on-demand
CMD ["tail", "-f", "/dev/null"]
