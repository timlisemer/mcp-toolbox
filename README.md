# MCP Toolbox

A Docker image that downloads and pre-builds Model Context Protocol (MCP)
servers for Claude Code, Codex, and other MCP clients.

## How it works

During the image build, the toolbox:

1. Reads tool definitions from `config/servers.json`.
2. Records remote HTTP MCP servers without creating local placeholders.
3. Verifies tools that the NixOS base image supplies.
4. Clones each remaining local server repository.
5. Installs its dependencies and builds its runtime artifacts.
6. Stores each built result under `/app/tools/<tool-name>/`.

The container stays alive so container-native stdio servers can be started with
`docker exec`. Remote servers are connected directly by the MCP client.

## Build environment

The toolbox is based on the multi-architecture
`ghcr.io/timlisemer/nixos-ci:latest` image published by the NixOS configuration
repository. Rust, Go, Node.js, Python, native libraries, and development
environment paths therefore come from the same declarations as the normal
NixOS workstations instead of a separate Debian package list.

The base image also supplies a matching Playwright MCP and Chromium pair. The
toolbox verifies this executable but does not clone Playwright, install its npm
dependencies, or download a browser.

The toolbox uses the host network and a 2 GiB shared-memory area. Playwright
can therefore open public sites and sites that listen on the container host,
including host services that listen only on `127.0.0.1`.

The base image is private. Image builds require a GitHub token with read access
to the `timlisemer/nixos-ci` package. Store it in the mcp-toolbox repository as
the Actions secret `NIXOS_REPO_TOKEN`; the workflow uses it only to pull the
base image.

## Available tools

| Tool | Type | Description |
| --- | --- | --- |
| mcp-nixos | Python | NixOS package and configuration search |
| tailwind-svelte-assistant | Node.js | Tailwind CSS and SvelteKit documentation |
| context7 | Node.js | Current library documentation and examples |
| playwright | Nix | Browser automation and page inspection |
| figma | Remote HTTP | Official Figma design context |
| blender | Python | Blender scene creation and rendering |
| astral-ai | Rust | Checking, planning, implementation, review, and Git workflows |

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

`just test` starts the Nix-built Chromium browser through Playwright MCP and
opens `https://example.com`. Set `PLAYWRIGHT_TEST_URL` to test a host site:

```bash
PLAYWRIGHT_TEST_URL=http://127.0.0.1:8000 just test
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
  /run/current-system/sw/bin/playwright-mcp --headless --browser chromium --no-sandbox
claude mcp add blender -- docker exec -i mcp-toolbox \
  /app/tools/blender/venv/bin/blender-mcp
```

Figma is an official remote MCP server:

```bash
claude mcp add --transport http figma https://mcp.figma.com/mcp
```

Authenticate Figma through the MCP client's connection management UI. The
toolbox does not store a Figma access token.

## Optional Astral AI service

Set `.tools["astral-ai"].enabled` in `config/servers.json` before you build the
image. The entry is enabled by default. Set it to `false` for a toolbox without
AI. Disabled mode needs no AI binaries, account, configuration, or mounts.
The base Compose file needs no AI setup when AI is disabled. If AI is enabled
but cannot start, the container exits with the service error.

The bundle uses the latest commit from the default branch (`main`) of
`https://github.com/timlisemer/astral-ai`. This is still the upstream
repository. Builds use locked dependencies and run `workspace-quality generate`.

The repository is private. When AI is enabled, image builds require a GitHub
token with read-only **Contents** access to `timlisemer/astral-ai`.
The existing Actions secret `ASTRAL_AI_REPO_TOKEN` supplies the BuildKit
secret `github_token`. For a local build:

```bash
docker build \
  --secret id=github_token,env=ASTRAL_AI_REPO_TOKEN \
  -t mcp-toolbox:latest .
```

The image keeps these regular files, including the shipped skill names:

```text
/app/bin/mcp-path-bridge
/app/tools/astral-ai/
├── bin/
│   ├── astral-ai
│   ├── astral-ai-mcp
│   └── astral-ai-tool-policy-hook
├── bridge/
│   └── astral-ai-paths.json
└── skills/
    └── astral-ai-*/
        └── SKILL.md
```

For AI hosting, use `docker-compose.astral-ai.yml` with the base Compose file.
It mounts host `/var/lib/astral-ai/instances/docker/control` at the same container path and
stores `/var/lib/astral-ai/data` in the persistent `astral-ai-data` volume.
Each control directory identifies one service instance. This Docker instance can run alongside Astral, which retains `/var/lib/astral-ai/control`. Select Docker ownership and create an account before you start the service.
Run these setup commands with the same mounts:

```bash
sudo mkdir -p /var/lib/astral-ai/instances/docker/control
docker-compose -f docker-compose.yml -f docker-compose.astral-ai.yml run --rm --no-deps mcp-toolbox \
  /app/tools/astral-ai/bin/astral-ai hosting select docker --if-unset \
  --control-dir /var/lib/astral-ai/instances/docker/control
docker-compose -f docker-compose.yml -f docker-compose.astral-ai.yml run --rm --no-deps mcp-toolbox \
  /app/tools/astral-ai/bin/astral-ai account bootstrap --if-unset --display-name "Local user" \
  --data-dir /var/lib/astral-ai/data
docker-compose -f docker-compose.yml -f docker-compose.astral-ai.yml up -d
```

The NixOS deployment runs `scripts/prepare-astral-ai.sh` in a separate provisioning container before starting the service. It uses the same persistent mounts and UID. Existing valid credentials are preserved; partial account state is rejected. Ordinary startup does not run these setup commands. Do not use `down -v` if you
need to keep the AI data volume. Use both Compose files for AI container
operations; the `just run` and `just restart` commands use only the base file.

Edit `config/astral-settings.toml` before building the image. It uses the same
settings format that Astral's Nix options generate. The service reads this file
directly. The supplied file selects the `opus` model tier and disables host
network access.
The Compose configuration explicitly sets `ASTRAL_AI_ADAPTER=codex`.
For other deployments, set `ASTRAL_AI_ADAPTER` explicitly to `codex` or `claude`.
The service rejects missing AI enablement, host enablement, model tier, network policy, or provider selection.

When enabled, `scripts/start-toolbox.sh` starts one service:

```bash
/app/tools/astral-ai/bin/astral-ai serve \
  --settings /app/config/astral-settings.toml \
  --mode docker \
  --control-dir /var/lib/astral-ai/instances/docker/control \
  --data-dir /var/lib/astral-ai/data \
  --socket /var/lib/astral-ai/data/service.sock
```

The startup script replaces itself with the AI service. The service receives
container shutdown directly. If it exits, the container exits. The image health
probe authenticates with the service; a running container alone is not a readiness
check. Use container logs to see errors. Restart after you correct the cause.

The image includes Codex and Claude executables for service-owned provider
processes. NixOS mounts `/home` at the same path and runs the container with the
selected user’s numeric ID. This preserves repository, credential, and transcript
paths. Client hooks use the same `session-bindings.sqlite3` database as the service.

Register the on-demand MCP client with the service socket and token file:

```bash
claude mcp add astral-ai --scope user -- docker exec -i \
  -e ASTRAL_AI_SOCKET=/var/lib/astral-ai/data/service.sock \
  -e ASTRAL_AI_TOKEN_FILE=/var/lib/astral-ai/data/token \
  mcp-toolbox /app/tools/astral-ai/bin/astral-ai-mcp
```

Each MCP invocation connects to the running service. It must not start another
runtime. The catalog entry contains the same socket and token file values.
`just test` checks the AI bundle only when the container's catalog entry is enabled.

## Windows path bridge

`mcp-path-bridge` changes declared path fields at two boundaries:

- `mcp-stdio` proxies newline-delimited MCP JSON-RPC messages.
- `hook` proxies one JSON hook request and one JSON hook response.

The hook mode is adapter-independent. A server path profile declares the hook
envelope selectors. The same bridge can support Codex, Claude, or another JSON
hook provider.

The bridge does not change all strings. Each MCP server owns a separate path
profile. A profile declares request fields, structured result fields, and hook
fields. An MCP server with no enabled profile gets no path conversion.
Astral AI creates its profile from Rust-owned JSON Schemas through its
existing `workspace-quality` generator. The Astral AI audit rejects a
missing or stale generated profile. The toolbox build runs that generator and
stores the generated file under `/app/tools/astral-ai/bridge/`.
The generated profile also declares the host-command bridge contract.
The MCP proxy gives this contract to Astral AI when it starts the server.

Each path mapping declares an `execution_host`. The value is `windows` when
Windows owns the mapped files. It is `linux` when Linux owns them. Astral
AI sends every repository command through one generic command bridge.
The bridge receives the executable, arguments, working directory, and explicit
environment changes. A command can also declare that its standard output is
one filesystem path. The bridge translates only this typed output and keeps
all other command output unchanged. It does not contain a list of tool names.
For a WSL profile, it starts a Windows executable through PowerShell when the
working directory has a Windows execution host. It starts the Linux executable
when the working directory has a Linux execution host.

The example file at `config/windows-bridge.example.json` has two execution
profiles:

- `wsl` uses `wsl.exe -d nixos`. Its example mappings cover the `C:` and `D:`
  drives and the WSL UNC root. Windows owns the drive mappings. Linux owns the
  WSL UNC mapping, which covers the complete Linux filesystem.
- `windows-remote` uses SSH. Its Windows and Linux prefixes are explicit
  configuration data. The bridge does not infer Linux paths from drive
  letters or select an execution host in this profile.

Use Nix configuration to select a profile and to generate the mapping list.
Pass the selected profile with `--profile` or set
`MCP_TOOLBOX_BRIDGE_PROFILE`. If neither value exists, `WSL=1` can select the
only configured WSL profile. The bridge then verifies `WSL_DISTRO_NAME`.
`WSL_INTEROP` supplies diagnostic information only. The remote profile does
not inspect WSL variables.

The `client-command` mode produces a `command` and `args` JSON object for any
Windows MCP or hook client. This example produces the WSL MCP command:

```bash
/path/to/mcp-path-bridge client-command \
  --config /path/to/windows-bridge.json \
  --profile wsl \
  --server astral-ai \
  --mode mcp-stdio \
  --bridge-command /path/to/mcp-path-bridge \
  --client-working-directory 'D:\repository' \
  -- docker exec -i \
    -e ASTRAL_AI_SOCKET=/var/lib/astral-ai/data/service.sock \
    -e ASTRAL_AI_TOKEN_FILE=/var/lib/astral-ai/data/token \
    mcp-toolbox /app/tools/astral-ai/bin/astral-ai-mcp
```

This example produces the remote hook command:

```bash
/path/to/mcp-path-bridge client-command \
  --config /path/to/windows-bridge.json \
  --profile windows-remote \
  --server astral-ai \
  --mode hook \
  --bridge-command /path/to/mcp-path-bridge \
  --client-working-directory 'D:\repository' \
  -- docker exec -i mcp-toolbox /app/tools/astral-ai/bin/astral-ai-tool-policy-hook tool-policy-hook
```

The path engine accepts slash and backslash forms of absolute Windows paths.
It compares Windows prefixes without case sensitivity. It uses the longest
full-component match. It converts separators and components in relative
Windows paths. It rejects ambiguous drive-relative and rooted-relative paths,
unmapped absolute Windows paths, Windows device paths, and UNC paths with no
explicit mapping. It converts declared Linux result paths and declared
single-path command output back across the boundary. Normal messages, logs,
opaque command output, and text results stay unchanged.

## Blender add-on

Blender MCP requires its companion add-on to be installed and running in
Blender. Copy it from the container, install it through **Blender > Edit >
Preferences > Add-ons**, enable **Interface: Blender MCP**, and click **Connect
to Claude** in the BlenderMCP sidebar:

```bash
docker cp mcp-toolbox:/app/tools/blender/addon.py ./blender-mcp-addon.py
```

The container reaches the add-on through `127.0.0.1` on port `9876`.
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
instead declares an HTTP `transport` and `url`. An optional `revision` pins a
Git commit or tag. Optional `patches` are applied before dependencies are
installed and fail the image build when they no longer apply cleanly. Private
GitHub repositories set `private_repository` to `true` and use the
`github_token` BuildKit secret.

## Project structure

```text
mcp-toolbox/
├── Dockerfile
├── docker-compose.yml
├── docker-compose.astral-ai.yml
├── justfile
├── config/servers.json
├── patches/
└── scripts/
    ├── install.sh
    └── start-toolbox.sh
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

### Readiness and client storage

When enabled, Astral AI is PID 1. A service failure stops the container. The image
health check performs an authenticated socket handshake; a running toolbox process
alone is not readiness. The disabled toolbox can still serve its other tools.

MCP clients and external policy hooks must use the same service data root. Set
`AGENTS_SDK_SESSION_BINDING_DATABASE=/var/lib/astral-ai/data/session-bindings.sqlite3`
for external hooks. This is where the running service redeems registered calls.
Mount caller repositories and transcripts at their original paths. The NixOS
deployment mounts `/home` and uses the host service user's UID and home, preserving
provider credentials and file ownership. Its Windows bridge runs inside the
container and receives the WSL and VM filesystem mounts.
