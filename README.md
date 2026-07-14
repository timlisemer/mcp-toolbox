# MCP Toolbox

A Docker container that downloads and pre-builds MCP (Model Context Protocol) servers, making them ready for use with Claude Code.

## How It Works

During image build, the container:

1. Reads tool definitions from `config/servers.json`
2. Records remote HTTP MCP servers without creating local placeholders
3. Clones each local tool's repository from GitHub
4. Installs dependencies (npm, pip, cargo, go modules)
5. Builds/compiles each local tool at `/app/tools/<tool-name>/`

The container stays running so tools can be invoked via `docker exec`. MCP tools use **stdio transport** - they read JSON-RPC from stdin and write to stdout, so each invocation is a fresh process (no persistent daemons).

Tools with `docker_volume: true` are stored in the `servers/` directory on the host, allowing persistent data and native execution outside Docker.

`agent-framework` is intentionally built through its own repo entrypoint:
`npm install && just build`. The image includes `just`, and mcp-toolbox does not
contain agent-framework-specific build logic. During `just build`,
agent-framework compiles itself and regenerates Codex hook trust hashes in
`adapters/codex/dotcodex/config.toml` from its own `hooks.json`.

## Available Tools

| Tool                      | Type    | Description                                     |
| ------------------------- | ------- | ----------------------------------------------- |
| mcp-nixos                 | Python  | NixOS package and configuration search          |
| tailwind-svelte-assistant | Node.js | Tailwind CSS and SvelteKit documentation        |
| context7                  | Node.js | Up-to-date code documentation for any library   |
| playwright                | Node.js | Browser automation and page inspection          |
| figma                     | Remote  | Official Figma design context and code generation |
| blender                   | Python  | Blender scene creation, inspection, and rendering |
| agent-framework           | Node.js | AI-powered code quality: check, confirm, commit |

## Quick Start

```bash
# Build and run
just build && just run

# Check available tools
just status

# Test a tool
echo '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | \
  docker exec -i mcp-toolbox /app/tools/mcp-nixos/venv/bin/python3 -m mcp_nixos.server
```

## Deploying Agent Framework

mcp-toolbox is the automated deployment path for agent-framework. At Docker
build time it clones the configured agent-framework repository, runs
`npm install && just build`, and stores the built repo as a volume-enabled tool.
At container start, the entrypoint refreshes `/app/servers/agent-framework`,
which is normally mounted to a host-owned volume such as
`/var/lib/mcp-toolbox/agent-framework`.

Manual Linux deployment is still possible from an agent-framework checkout:

```bash
cd /path/to/agent-framework
npm install
just build
cp -a adapters/claude/dotclaude/. ~/.claude/
cp -a adapters/codex/dotcodex/. ~/.codex/
```

Linux symlink deployment keeps host-agent config pointed at the built checkout:

```bash
cd /path/to/agent-framework
just build
ln -sfn "$PWD/adapters/claude/dotclaude/settings.json" ~/.claude/settings.json
ln -sfn "$PWD/adapters/codex/dotcodex/config.toml" ~/.codex/config.toml
ln -sfn "$PWD/adapters/codex/dotcodex/hooks.json" ~/.codex/hooks.json
```

Codex hook hashes are generated review fingerprints. They tell Codex that the
current hook commands in `hooks.json` have been reviewed and may run. They are
not credentials, and they change when a hook command, matcher, timeout, async
flag, or status message changes.

## Claude Code Configuration

Register the pre-built tools with Claude Code using `claude mcp add`:

```bash
# Tools running inside Docker container
claude mcp add nixos-search -- docker exec -i mcp-toolbox /app/tools/mcp-nixos/venv/bin/python3 -m mcp_nixos.server
claude mcp add tailwind-svelte -- docker exec -i mcp-toolbox node /app/tools/tailwind-svelte-assistant/run.mjs
claude mcp add context7 -- docker exec -i mcp-toolbox npx -y @upstash/context7-mcp
claude mcp add playwright -- docker exec -i mcp-toolbox node /app/tools/playwright/cli.js --headless --browser chromium --no-sandbox
claude mcp add blender -- docker exec -i mcp-toolbox /app/tools/blender/venv/bin/blender-mcp

# Figma is an official remote MCP server; connect the client directly.
claude mcp add --transport http figma https://mcp.figma.com/mcp

# agent-framework can run natively from the volume (docker_volume: true)
claude mcp add agent-framework -- node /path/to/mcp-toolbox/agent-framework/dist/src/mcp/server.js
# Or via Docker:
# claude mcp add agent-framework -- docker exec -i mcp-toolbox node /app/tools/agent-framework/dist/src/mcp/server.js
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
    },
    "playwright": {
      "command": "docker",
      "args": [
        "exec",
        "-i",
        "mcp-toolbox",
        "node",
        "/app/tools/playwright/cli.js",
        "--headless",
        "--browser",
        "chromium",
        "--no-sandbox"
      ]
    },
    "figma": {
      "type": "http",
      "url": "https://mcp.figma.com/mcp"
    },
    "blender": {
      "command": "docker",
      "args": [
        "exec",
        "-i",
        "mcp-toolbox",
        "/app/tools/blender/venv/bin/blender-mcp"
      ]
    }
  }
}
```

### Figma Authentication

Figma handles authentication through the MCP client. After registering the
remote server, open the client's MCP management UI and authenticate the
`figma` connection. No Figma access token is stored in this image.

### Blender Add-on

Blender MCP requires its companion add-on to be installed and running in
Blender. Copy the add-on from the built container, install it through
**Blender > Edit > Preferences > Add-ons**, enable **Interface: Blender MCP**,
then click **Connect to Claude** in the BlenderMCP sidebar:

```bash
docker cp mcp-toolbox:/app/tools/blender/addon.py ./blender-mcp-addon.py
```

The container connects to the host on port `9876` through
`host.docker.internal`. Override `BLENDER_HOST` or `BLENDER_PORT` in `.env` if
Blender runs elsewhere. Blender MCP can execute Python in Blender, so only use
it with MCP clients and prompts you trust.

### NixOS Integration Pattern

This is a generalized pattern for declarative NixOS or home-manager setups. It
captures the moving parts without assuming a particular host name, username,
network, secrets system, or volume layout.

#### Runtime Layout

Mount one host-owned directory to `/app/servers`. Tools with
`docker_volume: true` are copied there at container start and symlinked back into
`/app/tools`.

```text
/var/lib/mcp-toolbox/
└── agent-framework/
    ├── dist/src/mcp/server.js
    ├── adapters/claude/dotclaude/
    │   ├── settings.json
    │   ├── commands/
    │   └── agents/
    └── adapters/codex/dotcodex/
        ├── config.toml
        ├── hooks.json
        ├── skills/
        └── agents/
```

#### Environment Wrapper

If MCP servers or hooks need secrets, keep the secret source local to your
system and use a small wrapper to export variables before starting the process.

```bash
#!/usr/bin/env bash
set -euo pipefail

env_file="${MCP_TOOLBOX_ENV_FILE:-$HOME/.config/mcp-toolbox/env}"
if [[ -f "$env_file" ]]; then
  set -a
  source "$env_file"
  set +a
fi

exec "$@"
```

#### Client Adapter Files

agent-framework ships adapter-owned config for Claude Code and Codex. You can
link those files from the volume-enabled checkout instead of maintaining
separate copies.

```bash
MCP_TOOLBOX_ROOT=/var/lib/mcp-toolbox

mkdir -p "$HOME/.claude" "$HOME/.codex/skills" "$HOME/.codex/agents"

ln -sfn "$MCP_TOOLBOX_ROOT/agent-framework/adapters/claude/dotclaude/settings.json" \
  "$HOME/.claude/settings.json"
ln -sfn "$MCP_TOOLBOX_ROOT/agent-framework/adapters/claude/dotclaude/commands" \
  "$HOME/.claude/commands"
ln -sfn "$MCP_TOOLBOX_ROOT/agent-framework/adapters/claude/dotclaude/agents" \
  "$HOME/.claude/agents"

ln -sfn "$MCP_TOOLBOX_ROOT/agent-framework/adapters/codex/dotcodex/config.toml" \
  "$HOME/.codex/config.toml"
ln -sfn "$MCP_TOOLBOX_ROOT/agent-framework/adapters/codex/dotcodex/hooks.json" \
  "$HOME/.codex/hooks.json"

for agent in implementer implement-validator labeler tester; do
  ln -sfn "$MCP_TOOLBOX_ROOT/agent-framework/adapters/codex/dotcodex/agents/$agent.toml" \
    "$HOME/.codex/agents/$agent.toml"
done

for skill_dir in "$MCP_TOOLBOX_ROOT"/agent-framework/adapters/codex/dotcodex/skills/agent-framework-*; do
  [ -d "$skill_dir" ] || continue
  skill="$(basename "$skill_dir")"
  mkdir -p "$HOME/.codex/skills/$skill"
  cp "$skill_dir/SKILL.md" "$HOME/.codex/skills/$skill/SKILL.md"
done
```

#### NixOS Service Skeleton

```nix
{
  config,
  pkgs,
  lib,
  ...
}: let
  dockerBin = "${pkgs.docker}/bin/docker";
  volumeRoot = "/var/lib/mcp-toolbox";
  userNames = [ "alice" ];
in {
  virtualisation.oci-containers.containers.mcp-toolbox = {
    image = "ghcr.io/<owner>/mcp-toolbox/mcp-toolbox-linux-amd64:latest";
    autoStart = true;
    autoRemoveOnStop = true;
    volumes = [ "${volumeRoot}:/app/servers:rw" ];
    environment = {
      TELEMETRY_HOST_ID = config.networking.hostName;
    };
  };

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

      mkdir -p "${volumeRoot}"
      ${lib.concatMapStringsSep "\n      " (username: ''
        ${pkgs.acl}/bin/setfacl -R -m u:${username}:rwX "${volumeRoot}"
        ${pkgs.acl}/bin/setfacl -R -d -m u:${username}:rwX "${volumeRoot}"
      '') userNames}
    '';
  };

  system.activationScripts.claudeMcpSetup = {
    text = ''
      ${lib.concatMapStringsSep "\n      " (username: ''
        claudeBin="/home/${username}/.local/bin/claude"
        if [ -x "$claudeBin" ]; then
          ${pkgs.sudo}/bin/sudo -u ${username} "$claudeBin" mcp add nixos-search --scope user -- \
            ${dockerBin} exec -i mcp-toolbox sh -c 'exec 2>/dev/null; /app/tools/mcp-nixos/venv/bin/python3 -m mcp_nixos.server' >/dev/null 2>&1 || true
          ${pkgs.sudo}/bin/sudo -u ${username} "$claudeBin" mcp add tailwind-svelte --scope user -- \
            ${dockerBin} exec -i mcp-toolbox node /app/tools/tailwind-svelte-assistant/run.mjs >/dev/null 2>&1 || true
          ${pkgs.sudo}/bin/sudo -u ${username} "$claudeBin" mcp add context7 --scope user -- \
            ${dockerBin} exec -i mcp-toolbox npx -y @upstash/context7-mcp >/dev/null 2>&1 || true
          ${pkgs.sudo}/bin/sudo -u ${username} "$claudeBin" mcp add playwright --scope user -- \
            ${dockerBin} exec -i mcp-toolbox node /app/tools/playwright/cli.js --headless --browser chromium --no-sandbox >/dev/null 2>&1 || true
          ${pkgs.sudo}/bin/sudo -u ${username} "$claudeBin" mcp add figma --scope user --transport http \
            https://mcp.figma.com/mcp >/dev/null 2>&1 || true
          ${pkgs.sudo}/bin/sudo -u ${username} "$claudeBin" mcp add blender --scope user -- \
            ${dockerBin} exec -i mcp-toolbox /app/tools/blender/venv/bin/blender-mcp >/dev/null 2>&1 || true
          ${pkgs.sudo}/bin/sudo -u ${username} "$claudeBin" mcp add agent-framework --scope user -- \
            ${pkgs.nodejs}/bin/node ${volumeRoot}/agent-framework/dist/src/mcp/server.js >/dev/null 2>&1 || true
        fi
      '') userNames}
    '';
  };
}
```

For stricter systems, remove existing user-scoped MCP entries before adding
them, or manage the client settings file directly through home-manager. The
important details are:

1. Mount one persistent host directory to `/app/servers`.
2. Grant the interactive users read/write access to that host directory.
3. Register Docker-backed servers for tools that run inside the container.
4. Register remote HTTP servers such as Figma directly with the MCP client.
5. Register `agent-framework` natively from the volume at
   `agent-framework/dist/src/mcp/server.js`.
6. Link or copy the adapter-owned Claude and Codex config from
   `agent-framework/adapters/*`.

## Adding New Tools

1. Edit `config/servers.json` - add your tool definition
2. Run `just rebuild`

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
| `type`          | string  | Runtime type: `node`, `python`, `go`, `rust`, or `remote`               |
| `repository`    | string  | Git repository URL to clone                                             |
| `build_command` | string  | Command to build the tool after cloning                                 |
| `binary_path`   | string  | Path to the executable relative to tool directory                       |
| `transport`     | string  | Transport used by a remote server, such as `http`                       |
| `url`           | string  | Endpoint for a remote server; remote entries are not built locally      |
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
├── justfile             # Management commands
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
just build    # Build Docker image
just run      # Run container (foreground, Ctrl+C to stop)
just stop     # Stop container
just restart  # Restart container
just logs     # View container logs
just shell    # Open container shell
just status   # List available MCP tools
just test     # Test MCP tools respond
just check    # Validate config files
just clean    # Remove container and image
just rebuild  # Clean rebuild
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
  node /app/tools/agent-framework/dist/src/mcp/server.js

# Test agent-framework natively (from host, docker_volume: true)
echo '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | \
  node /path/to/mcp-toolbox/agent-framework/dist/src/mcp/server.js
```

### Check tool binaries exist

```bash
# Check all tools
docker exec mcp-toolbox ls -la /app/tools/

# Check volume-enabled tools (should show symlinks)
docker exec mcp-toolbox ls -la /app/tools/agent-framework
```
