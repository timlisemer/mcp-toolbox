# MCP Toolbox

A Docker image that downloads and pre-builds Model Context Protocol (MCP)
servers for Claude Code, Codex, and other MCP clients.

## How it works

During the image build, the toolbox:

1. Reads tool definitions from `config/servers.json`.
2. Records remote HTTP MCP servers without creating local placeholders.
3. Clones each local server repository.
4. Installs its dependencies and builds its runtime artifacts.
5. Stores the result under `/app/tools/<tool-name>/`.

The container stays alive so container-native stdio servers can be started with
`docker exec`. Remote servers are connected directly by the MCP client.

## Build environment

The toolbox is based on the multi-architecture
`ghcr.io/timlisemer/nixos-ci:latest` image published by the NixOS configuration
repository. Rust, Go, Node.js, Python, native libraries, and development
environment paths therefore come from the same declarations as the normal
NixOS workstations instead of a separate Debian package list.

## Available tools

| Tool | Type | Description |
| --- | --- | --- |
| mcp-nixos | Python | NixOS package and configuration search |
| tailwind-svelte-assistant | Node.js | Tailwind CSS and SvelteKit documentation |
| context7 | Node.js | Current library documentation and examples |
| playwright | Node.js | Browser automation and page inspection |
| figma | Remote HTTP | Official Figma design context |
| blender | Python | Blender scene creation and rendering |
| agent-framework | Rust | Checking, planning, implementation, review, and Git workflows |

## Quick start

```bash
just build
just run
```

In another shell:

```bash
just status
just test
```

## Client configuration

Container-native servers use stdio over `docker exec`:

```bash
claude mcp add nixos-search -- docker exec -i mcp-toolbox sh -c \
  'exec 2>/dev/null; /app/tools/mcp-nixos/venv/bin/python3 -m mcp_nixos.server'
claude mcp add tailwind-svelte -- docker exec -i mcp-toolbox \
  node /app/tools/tailwind-svelte-assistant/run.mjs
claude mcp add context7 -- docker exec -i mcp-toolbox \
  npx -y @upstash/context7-mcp
claude mcp add playwright -- docker exec -i mcp-toolbox \
  node /app/tools/playwright/cli.js --headless --browser chromium --no-sandbox
claude mcp add blender -- docker exec -i mcp-toolbox \
  /app/tools/blender/venv/bin/blender-mcp
```

Figma is an official remote MCP server:

```bash
claude mcp add --transport http figma https://mcp.figma.com/mcp
```

Authenticate Figma through the MCP client's connection management UI. The
toolbox does not store a Figma access token.

## Deploying agent-framework-rs

The old TypeScript `agent-framework` checkout, Node runtime, adapter files,
global hooks, persistent Docker volume, and symlink deployment are not part of
this image. The replacement is built from
`https://github.com/timlisemer/agent-framework-rs` with the repository's locked
dependencies.

The repository is private. Image builds require a fine-grained GitHub token
with read-only **Contents** access to `timlisemer/agent-framework-rs`. Store it
in the mcp-toolbox repository as the Actions secret
`AGENT_FRAMEWORK_REPO_TOKEN`; the workflow passes it to BuildKit as
`github_token`, and the credential is not retained in the image.

For a local build, expose the same token through an environment variable:

```bash
docker build \
  --secret id=github_token,env=AGENT_FRAMEWORK_REPO_TOKEN \
  -t mcp-toolbox:latest .
```

The image retains the two release executables and the canonical,
adapter-independent skill bundle:

```text
/app/tools/agent-framework/
├── bin/
│   ├── agent-framework-mcp
│   └── agent-framework-tool-policy-hook
└── skills/
    └── agent-framework-*/
        └── SKILL.md
```

`workspace-quality generate` produces every `SKILL.md` from one Rust-owned
inventory. There are no separate Claude and Codex copies to drift apart; both
clients receive the same generated files.

`agent-framework-mcp` is the stdio MCP server.
`agent-framework-tool-policy-hook` is the provider hook policy host. The MCP
server discovers it next to its own executable for request-scoped
agent-framework workflows. For top-level Claude and Codex sessions, the host
generates the corresponding per-user settings and hook files, and copies the
same skill bundle into both clients. Both executables and every skill artifact
must remain regular files; the deployment does not use symlinks.

Unlike the other local servers, agent-framework must run on the host. Its
workflows inspect host repositories and launch the host's authenticated
`claude` or `codex` executable. Running it through `docker exec` would isolate
the repositories, client executables, configuration, and credentials it needs.

Copy the bundle from a running toolbox container to a host-owned directory:

```bash
mkdir -p /path/to/agent-framework/bin /path/to/agent-framework/skills
docker cp mcp-toolbox:/app/tools/agent-framework/bin/. \
  /path/to/agent-framework/bin/
docker cp mcp-toolbox:/app/tools/agent-framework/skills/. \
  /path/to/agent-framework/skills/
chmod 755 /path/to/agent-framework/bin/agent-framework-*
```

The default provider is Claude Code. Select Codex with
`AGENT_FRAMEWORK_ADAPTER=codex`:

```bash
# Claude Code provider
claude mcp add agent-framework --scope user -- \
  env AGENT_FRAMEWORK_ADAPTER=claude \
  /path/to/agent-framework/bin/agent-framework-mcp

# Direct protocol probe using the Codex provider
printf '%s\n' \
  '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"probe","version":"1"}}}' \
  | AGENT_FRAMEWORK_ADAPTER=codex \
    /path/to/agent-framework/bin/agent-framework-mcp
```

The MCP wire names remain `check`, `validate_plan`, `create_planfile`,
`implement`, `validate_implementation`, `confirm`, `fullconfirm`, `commit`,
`push`, `list_repos`, `validate_intent`, `transcript`, `locate_scenario`, and
`scenario_tester`.

### NixOS deployment shape

A declarative NixOS deployment should:

1. Start the toolbox container without a persistent agent-framework volume.
2. Copy both release executables and the canonical skill bundle from the image
   into a host directory.
3. Ensure the copied executables are regular and executable, and reject any
   symlink in the skill bundle.
4. Register `agent-framework-mcp` as a host-native MCP server with the correct
   `AGENT_FRAMEWORK_ADAPTER` value.
5. Copy the host-generated Claude settings and Codex hook definition into each
   user's home as regular, user-owned files. Replace them on every rebuild but
   leave them mutable between rebuilds. Generate the matching Codex hook trust
   state in the user's writable `config.toml`.
6. Copy every canonical `agent-framework-*` skill directory into both
   `~/.claude/skills` and `~/.codex/skills` as regular, mutable, user-owned
   files. Replace only framework-owned skills on every rebuild.
7. Keep the host's `claude`, `codex`, credentials, and repository paths
   available through the normal user environment.

No agent-framework `.env`, adapter-specific command directory, or agent
directory is required.

## Blender add-on

Blender MCP requires its companion add-on to be installed and running in
Blender. Copy it from the container, install it through **Blender > Edit >
Preferences > Add-ons**, enable **Interface: Blender MCP**, and click **Connect
to Claude** in the BlenderMCP sidebar:

```bash
docker cp mcp-toolbox:/app/tools/blender/addon.py ./blender-mcp-addon.py
```

The container reaches the add-on through `host.docker.internal` on port `9876`.
Override `BLENDER_HOST` or `BLENDER_PORT` in `.env` when needed. Blender MCP can
execute Python in Blender, so use it only with clients and prompts you trust.

## Adding tools

Add a definition to `config/servers.json`, then run `just rebuild`.

```json
{
  "tools": {
    "my-tool": {
      "enabled": true,
      "type": "node",
      "description": "What the tool does",
      "repository": "https://github.com/user/repo",
      "private_repository": false,
      "build_command": "npm install && npm run build",
      "binary_path": "dist/index.js",
      "install_path": "dist/index.js",
      "capabilities": ["feature1"],
      "default_args": [],
      "environment": {}
    }
  }
}
```

Supported local types are `node`, `python`, `go`, and `rust`. A `remote` entry
instead declares an HTTP `transport` and `url`. Optional `patches` are applied
before dependencies are installed and fail the image build when they no longer
apply cleanly. Private GitHub repositories set `private_repository` to `true`
and use the `github_token` BuildKit secret.

## Project structure

```text
mcp-toolbox/
├── Dockerfile
├── docker-compose.yml
├── justfile
├── config/servers.json
├── patches/
└── scripts/install.sh
```

## Commands

```text
just build    Build the Docker image
just run      Run the container in the foreground
just stop     Stop the container
just restart  Restart the container
just logs     Follow container logs
just shell    Open a shell in the container
just status   List enabled tools
just test     Probe runtime tools and retained artifacts
just check    Validate configuration and scripts
just clean    Remove the local container and image
just rebuild  Clean, build, and run
```
