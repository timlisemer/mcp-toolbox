# MCP Toolbox

A Docker container that downloads and pre-builds MCP (Model Context Protocol) servers, making them ready for use with Claude Code.

## How It Works

During image build, the container:

1. Reads tool definitions from `config/servers.json`
2. Clones each tool's repository from GitHub
3. Installs dependencies (npm, pip, cargo, go modules)
4. Builds/compiles each tool
5. Packages everything at `/app/tools/<tool-name>/`

The container stays running so tools can be invoked via `docker exec`. MCP tools use **stdio transport** - they read JSON-RPC from stdin and write to stdout, so each invocation is a fresh process (no persistent daemons).

Tools with `docker_volume: true` are stored in the `servers/` directory on the host, allowing persistent data and native execution outside Docker.

## Available Tools

| Tool                      | Type    | Description                                     |
| ------------------------- | ------- | ----------------------------------------------- |
| mcp-nixos                 | Python  | NixOS package and configuration search          |
| tailwind-svelte-assistant | Node.js | Tailwind CSS and SvelteKit documentation        |
| context7                  | Node.js | Up-to-date code documentation for any library   |
| agent-framework           | Node.js | AI-powered code quality: check, confirm, commit |

## Quick Start

```bash
# Build and run
make build && make run

# Check available tools
make status

# Test a tool
echo '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | \
  docker exec -i mcp-toolbox /app/tools/mcp-nixos/venv/bin/python3 -m mcp_nixos.server
```

## Claude Code Configuration

Register the pre-built tools with Claude Code using `claude mcp add`:

```bash
# Tools running inside Docker container
claude mcp add nixos-search -- docker exec -i mcp-toolbox /app/tools/mcp-nixos/venv/bin/python3 -m mcp_nixos.server
claude mcp add tailwind-svelte -- docker exec -i mcp-toolbox node /app/tools/tailwind-svelte-assistant/run.mjs
claude mcp add context7 -- docker exec -i mcp-toolbox npx -y @upstash/context7-mcp

# agent-framework can run natively from the volume (docker_volume: true)
claude mcp add agent-framework -- node /path/to/mcp-server-host/servers/agent-framework/dist/mcp/server.js
# Or via Docker:
# claude mcp add agent-framework -- docker exec -i mcp-toolbox node /app/tools/agent-framework/dist/mcp/server.js
```

Or add to your Claude Code MCP settings (`~/.claude/settings.json`):

```json
{
  "mcpServers": {
    "nixos-search": {
      "command": "docker",
      "args": [
        "exec",
        "-i",
        "mcp-toolbox",
        "/app/tools/mcp-nixos/venv/bin/python3",
        "-m",
        "mcp_nixos.server"
      ]
    },
    "context7": {
      "command": "docker",
      "args": [
        "exec",
        "-i",
        "mcp-toolbox",
        "npx",
        "-y",
        "@upstash/context7-mcp"
      ]
    }
  }
}
```

### NixOS + Claude Code Integration Example

This setup demonstrates declarative management of Claude Code hooks and MCP servers using NixOS + home-manager, with agent-framework providing code quality tooling.

#### Architecture

```
Claude Code
├── Hooks (triggered on events)
│   ├── PreToolUse  → agent-framework pre-tool-use.js
│   ├── PostToolUse → agent-framework post-tool-use.js
│   └── Stop        → agent-framework stop-off-topic-check.js
│
├── MCP Servers (callable tools)
│   ├── agent-framework → check, confirm, commit
│   ├── nixos-search    → NixOS package/option search
│   ├── context7        → Documentation lookup
│   └── tailwind-svelte → Frontend assistance
│
└── Environment
    └── env.sh → Loads secrets (API keys, webhooks)
```

#### Claude Code Settings (`~/.claude/settings.json`)

```json
{
  "$schema": "https://json.schemastore.org/claude-code-settings.json",
  "alwaysThinkingEnabled": true,
  "permissions": {
    "allow": [
      "Bash(grep:*)",
      "Bash(find:*)",
      "WebFetch(domain:github.com)",
      "WebFetch(domain:docs.rs)",
      "WebFetch(domain:nixos.org)",
      "WebFetch(domain:tailwindcss.com)",
      "WebFetch(domain:svelte.dev)",
      "WebFetch(domain:tauri.app)"
    ],
    "deny": []
  },
  "hooks": {
    "PreToolUse": [
      {
        "matcher": ".*",
        "hooks": [
          {
            "type": "command",
            "command": "$HOME/.claude/hooks/pre-tool-use.sh",
            "timeout": 30
          }
        ]
      }
    ],
    "PostToolUse": [
      {
        "matcher": ".*",
        "hooks": [
          {
            "type": "command",
            "command": "$HOME/.claude/hooks/post-tool-use.sh",
            "timeout": 30
          }
        ]
      }
    ],
    "Stop": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "$HOME/.claude/hooks/stop-off-topic-check.sh",
            "timeout": 30
          }
        ]
      }
    ]
  }
}
```

#### Environment Setup (`~/.claude/env.sh`)

```bash
#!/usr/bin/env bash
# Shared environment setup for Claude Code hooks, MCP servers, and commands
# Called via run-with-env.sh wrapper

# Source API keys from SOPS secrets (with auto-export)
if [[ -f /run/secrets/mcpToolboxENV ]]; then
  set -a # Enable auto-export
  source /run/secrets/mcpToolboxENV
  set +a # Disable auto-export
fi

# Export webhook secrets for hook scripts
if [[ -f /run/secrets/webhook_id_agent_logs ]]; then
  export WEBHOOK_ID_AGENT_LOGS=$(cat /run/secrets/webhook_id_agent_logs)
fi
```

#### Environment Wrapper (`~/.claude/run-with-env.sh`)

```bash
#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/env.sh"
exec "$@"
```

#### Hook Scripts

**`~/.claude/hooks/pre-tool-use.sh`**

```bash
#!/usr/bin/env bash
exec "$(dirname "$0")/../run-with-env.sh" node /mnt/docker-data/volumes/mcp-toolbox/agent-framework/dist/hooks/pre-tool-use.js
```

**`~/.claude/hooks/post-tool-use.sh`**

```bash
#!/usr/bin/env bash
exec "$(dirname "$0")/../run-with-env.sh" node /mnt/docker-data/volumes/mcp-toolbox/agent-framework/dist/hooks/post-tool-use.js
```

**`~/.claude/hooks/stop-off-topic-check.sh`**

```bash
#!/usr/bin/env bash
exec "$(dirname "$0")/../run-with-env.sh" node /mnt/docker-data/volumes/mcp-toolbox/agent-framework/dist/hooks/stop-off-topic-check.js
```

#### NixOS Module (`services/mcp-toolbox.nix`)

```nix
{
  config,
  pkgs,
  inputs,
  lib,
  ...
}: let
  dockerBin = "${pkgs.docker}/bin/docker";
  mcpToolboxImage =
    if pkgs.stdenv.hostPlatform.system == "aarch64-linux"
    then "ghcr.io/timlisemer/mcp-toolbox/mcp-toolbox-linux-arm64:latest"
    else "ghcr.io/timlisemer/mcp-toolbox/mcp-toolbox-linux-amd64:latest";
  unstable = import inputs.nixpkgs-unstable {
    config = {allowUnfree = true;};
    system = pkgs.stdenv.hostPlatform.system;
  };
in {
  ##########################################################################
  ## MCP Toolbox Docker Container                                         ##
  ##########################################################################
  virtualisation.oci-containers.containers.mcp-toolbox = {
    image = mcpToolboxImage;
    autoStart = true;

    autoRemoveOnStop = true;
    extraOptions = ["--network=docker-network" "--ip=172.18.0.15"];

    volumes = [
      "/mnt/docker-data/volumes/mcp-toolbox:/app/servers:rw"
    ];

    environmentFiles = [
      "/run/secrets/mcpToolboxENV"
    ];
  };

  ##########################################################################
  ## MCP Toolbox volume permissions - make accessible to users            ##
  ##########################################################################
  systemd.services.mcp-toolbox-permissions = {
    description = "Set permissions on mcp-toolbox volume for user access";
    after = ["docker.service" "docker-mcp-toolbox.service"];
    wantedBy = ["multi-user.target"];

    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
    };

    script = ''
      set -euo pipefail

      VOLUME_PATH="/mnt/docker-data/volumes/mcp-toolbox"

      if [ -d "$VOLUME_PATH" ]; then
        echo "Setting permissions on $VOLUME_PATH..."

        # Set execute-only ACL on parent directories for traversal (no read access)
        ${pkgs.acl}/bin/setfacl -m u:tim:x /mnt/docker-data
        ${pkgs.acl}/bin/setfacl -m u:tim:x /mnt/docker-data/volumes

        # Set full access on the volume directory and contents
        ${pkgs.acl}/bin/setfacl -R -m u:tim:rwX "$VOLUME_PATH"
        # Set default ACL so new files inherit permissions
        ${pkgs.acl}/bin/setfacl -R -d -m u:tim:rwX "$VOLUME_PATH"

        echo "MCP Toolbox volume permissions set successfully"
      else
        echo "Warning: $VOLUME_PATH does not exist yet"
      fi
    '';
  };

  ##########################################################################
  ## Setup Claude MCP servers                                             ##
  ##########################################################################
  system.activationScripts.claudeMcpSetup = {
    text = ''
      echo "[claude-mcp] Setting up MCP servers..."

      # Run as tim user since claude config is per-user
      ${pkgs.sudo}/bin/sudo -u tim ${unstable.claude-code}/bin/claude mcp list 2>/dev/null | ${pkgs.gawk}/bin/awk -F: '/^[a-zA-Z0-9_-]+:/ {print $1}' | while read -r server; do
        echo "[claude-mcp] Removing server: $server"
        ${pkgs.sudo}/bin/sudo -u tim ${unstable.claude-code}/bin/claude mcp remove --scope user "$server" 2>/dev/null || true
      done

      echo "[claude-mcp] Adding nixos-search server..."
      ${pkgs.sudo}/bin/sudo -u tim ${unstable.claude-code}/bin/claude mcp add nixos-search --scope user -- ${dockerBin} exec -i mcp-toolbox sh -c 'exec 2>/dev/null; /app/tools/mcp-nixos/venv/bin/python3 -m mcp_nixos.server'

      echo "[claude-mcp] Adding tailwind-svelte server..."
      ${pkgs.sudo}/bin/sudo -u tim ${unstable.claude-code}/bin/claude mcp add tailwind-svelte --scope user -- ${dockerBin} exec -i mcp-toolbox node /app/tools/tailwind-svelte-assistant/run.mjs

      echo "[claude-mcp] Adding context7 server..."
      ${pkgs.sudo}/bin/sudo -u tim ${unstable.claude-code}/bin/claude mcp add context7 --scope user -- ${dockerBin} exec -i mcp-toolbox npx -y @upstash/context7-mcp

      echo "[claude-mcp] Adding agent-framework server..."
      ${pkgs.sudo}/bin/sudo -u tim ${unstable.claude-code}/bin/claude mcp add agent-framework --scope user -- \
        /home/tim/.claude/run-with-env.sh ${pkgs.nodejs}/bin/node /mnt/docker-data/volumes/mcp-toolbox/agent-framework/dist/mcp/server.js


      echo "[claude-mcp] MCP servers setup complete"
    '';
  };

  ##########################################################################
  ## Claude Code shared environment and hooks                             ##
  ##########################################################################
  home-manager.sharedModules = [
    {
      home.file = {
        # Claude Code shared environment
        ".claude/env.sh" = {
          source = builtins.toPath ../files/.claude/env.sh;
          executable = true;
        };
        ".claude/hooks/pre-tool-use.sh" = {
          source = builtins.toPath ../files/.claude/hooks/pre-tool-use.sh;
          executable = true;
        };
        ".claude/hooks/stop-off-topic-check.sh" = {
          source = builtins.toPath ../files/.claude/hooks/stop-off-topic-check.sh;
          executable = true;
        };
        ".claude/hooks/post-tool-use.sh" = {
          source = builtins.toPath ../files/.claude/hooks/post-tool-use.sh;
          executable = true;
        };
        ".claude/run-with-env.sh" = {
          source = builtins.toPath ../files/.claude/run-with-env.sh;
          executable = true;
        };
        # Claude Code commands
        ".claude/commands/commit.md" = {
          source = builtins.toPath ../files/.claude/commands/commit.md;
        };
        ".claude/commands/push.md" = {
          source = builtins.toPath ../files/.claude/commands/push.md;
        };
      };
    }
  ];
}
```

#### Directory Structure

```
~/.claude/
├── env.sh                    # Environment variables (secrets)
├── run-with-env.sh           # Wrapper that sources env.sh
├── settings.json             # Claude Code settings + hooks
├── hooks/
│   ├── pre-tool-use.sh       # PreToolUse hook
│   ├── post-tool-use.sh      # PostToolUse hook
│   └── stop-off-topic-check.sh # Stop hook
└── commands/
    ├── commit.md             # /commit slash command
    └── push.md               # /push slash command

/mnt/docker-data/volumes/mcp-toolbox/agent-framework/
└── dist/
    ├── hooks/
    │   ├── pre-tool-use.js
    │   ├── post-tool-use.js
    │   └── stop-off-topic-check.js
    └── mcp/
        └── server.js         # MCP server (check, confirm, commit tools)
```

#### How It Works

1. NixOS rebuild deploys hook scripts to `~/.claude/` via home-manager
2. Activation script registers MCP servers with `claude mcp add`
3. Hooks load environment via `run-with-env.sh` -> `env.sh` before calling agent-framework JS
4. MCP tools (check, confirm, commit) are available to Claude during conversations
5. Secrets are loaded from SOPS-managed files at runtime

## Adding New Tools

1. Edit `config/servers.json` - add your tool definition
2. Run `make rebuild`

### Tool Configuration

```json
{
  "tools": {
    "my-tool": {
      "enabled": true,
      "docker_volume": false,
      "type": "node",
      "description": "What the tool does",
      "repository": "https://github.com/user/repo",
      "build_command": "npm install && npm run build",
      "binary_path": "dist/index.js",
      "capabilities": ["feature1", "feature2"]
    }
  }
}
```

**Configuration Options:**

| Option          | Type    | Description                                                             |
| --------------- | ------- | ----------------------------------------------------------------------- |
| `enabled`       | boolean | Whether to build and enable this tool                                   |
| `docker_volume` | boolean | If `true`, tool data persists in `servers/<name>/` and can run natively |
| `type`          | string  | Runtime type: `node`, `python`, `go`, `rust`                            |
| `repository`    | string  | Git repository URL to clone                                             |
| `build_command` | string  | Command to build the tool after cloning                                 |
| `binary_path`   | string  | Path to the executable relative to tool directory                       |
| `capabilities`  | array   | List of tool capabilities (documentation only)                          |

### Docker Volume Feature

When `docker_volume: true` is set for a tool:

1. **Build time**: Tool is built normally, then moved to `/app/tools-builtin/<name>/`
2. **First run**: Built artifacts are copied to `/app/servers/<name>/` (mounted volume)
3. **Runtime**: A symlink `/app/tools/<name>/` -> `/app/servers/<name>/` is created

This allows:

- **Persistent data**: Tool data survives container rebuilds
- **Native execution**: Tools can be run directly from `servers/<name>/` on the host without Docker
- **Easy updates**: Modify tool code directly in the volume

Currently enabled for: `agent-framework`

## Project Structure

```
mcp-toolbox/
├── Dockerfile           # Build environment with Node/Python/Go/Rust
├── docker-compose.yml   # Container configuration
├── Makefile             # Management commands
├── config/
│   └── servers.json     # Tool definitions
├── scripts/
│   ├── install.sh       # Build script for all tools
│   └── entrypoint.sh    # Runtime initialization (symlinks, volume setup)
└── servers/             # Persistent storage for docker_volume tools (git-ignored)
    └── <tool-name>/     # Tool data (e.g., servers/agent-framework/)
```

## Commands

```bash
make build    # Build Docker image
make run      # Run container (foreground, Ctrl+C to stop)
make stop     # Stop container
make restart  # Restart container
make logs     # View container logs
make shell    # Open container shell
make status   # List available MCP tools
make test     # Test MCP tools respond
make clean    # Remove container and image
make rebuild  # Clean rebuild
```

## Environment Variables

The `agent-framework` tool requires API credentials. Two options are supported:

**Option A: Direct Anthropic API**

```bash
ANTHROPIC_API_KEY=sk-ant-...
```

**Option B: OpenRouter (Anthropic-compatible)**

```bash
ANTHROPIC_API_KEY=           # Leave empty
ANTHROPIC_BASE_URL=https://openrouter.ai/api
ANTHROPIC_AUTH_TOKEN=sk-or-...  # Your OpenRouter key
```

See `.env.example` for the full template.

## Troubleshooting

### Test a tool manually

```bash
# Enter the container
docker exec -it mcp-toolbox /bin/bash

# Test mcp-nixos (inside container)
echo '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | \
  /app/tools/mcp-nixos/venv/bin/python3 -m mcp_nixos.server

# Test agent-framework (inside container via symlink)
echo '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | \
  node /app/tools/agent-framework/dist/mcp/server.js

# Test agent-framework natively (from host, docker_volume: true)
echo '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | \
  node /path/to/mcp-server-host/agent-framework/dist/mcp/server.js
```

### Check tool binaries exist

```bash
# Check all tools
docker exec mcp-toolbox ls -la /app/tools/

# Check volume-enabled tools (should show symlinks)
docker exec mcp-toolbox ls -la /app/tools/agent-framework
```
