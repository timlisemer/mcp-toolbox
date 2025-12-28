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
claude mcp add nixos-search -- docker exec -i mcp-toolbox /app/tools/mcp-nixos/venv/bin/python3 -m mcp_nixos.server
claude mcp add tailwind-svelte --scope user -- docker exec -i mcp-toolbox node /app/tools/tailwind-svelte-assistant/dist/index.js
claude mcp add context7 -- docker exec -i mcp-toolbox npx -y @upstash/context7-mcp
claude mcp add agent-framework -- docker exec -i mcp-toolbox node /app/tools/agent-framework/dist/mcp/server.js
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

### NixOS Integration

On NixOS, you can use a systemd user service to automatically configure MCP servers on login:

```nix
systemd.user.services.claude-mcp-setup = {
  Unit = {
    Description = "Setup Claude MCP servers";
    After = ["network-online.target"];
  };
  Service = {
    Type = "oneshot";
    ExecStart = "${pkgs.writeShellScript "claude-mcp-setup" ''
      # Add MCP servers pointing to the toolbox container
      claude mcp add nixos-search --scope user -- docker exec -i mcp-toolbox \
        /app/tools/mcp-nixos/venv/bin/python3 -m mcp_nixos.server
      claude mcp add context7 --scope user -- docker exec -i mcp-toolbox \
        npx -y @upstash/context7-mcp
    ''}";
  };
  Install.WantedBy = ["default.target"];
};
```

## Adding New Tools

1. Edit `config/servers.json` - add your tool definition
2. Run `make rebuild`

### Tool Configuration

```json
{
  "tools": {
    "my-tool": {
      "enabled": true,
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

Supported types: `node`, `python`, `go`, `rust`

## Project Structure

```
mcp-toolbox/
├── Dockerfile           # Build environment with Node/Python/Go/Rust
├── docker-compose.yml   # Container configuration
├── Makefile             # Management commands
├── config/
│   └── servers.json     # Tool definitions
└── scripts/
    └── install.sh       # Build script for all tools
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

# Test mcp-nixos
echo '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | \
  /app/tools/mcp-nixos/venv/bin/python3 -m mcp_nixos.server

# Test agent-framework
echo '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | \
  node /app/tools/agent-framework/dist/mcp/server.js
```

### Check tool binaries exist

```bash
docker exec mcp-toolbox ls -la /app/tools/
```
