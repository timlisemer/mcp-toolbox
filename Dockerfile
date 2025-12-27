# MCP Toolbox - Pre-builds MCP tools for on-demand invocation
FROM ubuntu:25.04

# Install runtimes and build tools
RUN apt-get update && apt-get install -y \
    curl \
    wget \
    git \
    build-essential \
    ca-certificates \
    python3 \
    python3-pip \
    python3-venv \
    jq \
    && rm -rf /var/lib/apt/lists/*

# Install Node.js 20
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y nodejs

# Install Go 1.21
RUN wget https://go.dev/dl/go1.21.5.linux-amd64.tar.gz && \
    tar -C /usr/local -xzf go1.21.5.linux-amd64.tar.gz && \
    rm go1.21.5.linux-amd64.tar.gz
ENV PATH="/usr/local/go/bin:/root/go/bin:${PATH}"
ENV GOPATH="/root/go"

# Install Rust + rust-analyzer
RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
ENV PATH="/root/.cargo/bin:${PATH}"
RUN rustup component add rust-src rust-analyzer

# Create directory structure
WORKDIR /app
RUN mkdir -p /app/tools /app/config /workspace

# Copy configuration and build script
COPY config/ /app/config/
COPY scripts/install.sh /app/scripts/
RUN chmod +x /app/scripts/*.sh

# Pre-build all MCP tools
RUN /app/scripts/install.sh

# Workspace for projects
VOLUME ["/workspace"]

# Stay alive for docker exec access - tools are invoked on-demand
CMD ["tail", "-f", "/dev/null"]
