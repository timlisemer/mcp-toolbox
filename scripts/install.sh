#!/bin/bash
set -e

CONFIG_FILE="${MCP_SERVERS_CONFIG:-/app/config/servers.json}"
TOOLS_DIR="/app/tools"

echo "Pre-building MCP tools from $CONFIG_FILE..."

# Parse tools configuration
tools=$(jq -r '.tools | to_entries[] | select(.value.enabled == true) | @json' "$CONFIG_FILE")

while IFS= read -r tool_json; do
    tool=$(echo "$tool_json" | jq -r '.')
    name=$(echo "$tool" | jq -r '.key')
    value=$(echo "$tool" | jq -r '.value')

    type=$(echo "$value" | jq -r '.type')
    repo=$(echo "$value" | jq -r '.repository')
    build_cmd=$(echo "$value" | jq -r '.build_command')
    description=$(echo "$value" | jq -r '.description')
    patches=$(echo "$value" | jq -r '.patches[]?')

    echo ""
    echo "Building $name ($type)..."
    echo "  Description: $description"
    echo "  Repository: $repo"

    # Remote MCP servers are enabled client-side and do not have local build
    # artifacts. Keep them in the catalog/status output without creating a
    # misleading placeholder executable.
    if [ "$type" = "remote" ]; then
        url=$(echo "$value" | jq -r '.url')
        echo "  Remote endpoint: $url"
        echo "  No local build required for $name"
        continue
    fi

    # Create tool directory
    tool_dir="$TOOLS_DIR/$name"
    mkdir -p "$tool_dir"
    cd "$tool_dir"

    # Clone repository if it exists and is valid
    if [[ "$repo" == http* ]]; then
        if git ls-remote "$repo" &>/dev/null; then
            git clone --depth 1 "$repo" . 2>/dev/null || echo "  Using cached repository"
        else
            echo "  Warning: Repository not accessible, skipping $name"
            continue
        fi
    else
        echo "  Info: No valid repository URL, creating placeholder for $name"
        # Create a placeholder for tools without real repos
        case "$type" in
        "node")
            echo '{"name": "'$name'", "version": "1.0.0"}' >package.json
            echo 'console.log("MCP Tool: '$name'");' >index.js
            ;;
        "go")
            echo 'package main; import "fmt"; func main() { fmt.Println("MCP Tool: '$name'") }' >main.go
            ;;
        "rust")
            cargo init --name "$name" 2>/dev/null || true
            ;;
        esac
    fi

    # Apply repository-specific compatibility fixes before dependencies are
    # installed. Patches deliberately fail the image build when upstream code
    # changes, so an outdated fix cannot be silently omitted.
    while IFS= read -r patch_name; do
        if [ -z "$patch_name" ]; then
            continue
        fi

        patch_file="/app/patches/$patch_name"
        if [ ! -f "$patch_file" ]; then
            echo "  Error: Patch not found: $patch_file" >&2
            exit 1
        fi

        echo "  Applying patch: $patch_name"
        git apply --check "$patch_file"
        git apply "$patch_file"
    done <<< "$patches"

    # Build based on type
    echo "  Compiling..."
    case "$type" in
    "go")
        if [ -f "go.mod" ] || [ -f "main.go" ]; then
            eval "$build_cmd" || echo "  Build skipped (placeholder)"
        fi
        ;;
    "rust")
        if [ -f "Cargo.toml" ]; then
            eval "$build_cmd" || echo "  Build skipped (placeholder)"
        fi
        ;;
    "node")
        if [ -f "package.json" ]; then
            eval "$build_cmd" || echo "  Build failed"
        fi
        # Create stdio wrapper for Smithery-based servers
        if [ "$name" = "tailwind-svelte-assistant" ]; then
            echo "  Creating stdio runner wrapper..."
            cat >run.mjs <<'RUNNER_EOF'
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import createServer from "./dist/index.js";

const server = createServer();
const transport = new StdioServerTransport();
await server.connect(transport);
RUNNER_EOF
        fi
        ;;
    "python")
        # Create and activate virtual environment for this tool
        echo "  Creating virtual environment..."
        python3 -m venv venv
        source venv/bin/activate

        if [ -f "requirements.txt" ]; then
            pip install -r requirements.txt || echo "  Dependencies skipped"
        fi
        # Execute the build command for Python tools (e.g., pip install)
        if [[ "$build_cmd" == pip* ]]; then
            # Replace pip3 with pip in venv
            venv_build_cmd="${build_cmd/pip3/pip}"
            eval "$venv_build_cmd" || echo "  Build command failed: $venv_build_cmd"
        elif [ -f "setup.py" ] || [ -f "pyproject.toml" ]; then
            pip install -e . || echo "  Local install failed"
        fi
        deactivate
        ;;
    esac

    echo "  Build complete for $name"

done <<<"$tools"

echo ""
echo "All MCP tools built successfully!"
echo "Tools directory: $TOOLS_DIR"
ls -la "$TOOLS_DIR" 2>/dev/null || true

# Move volume-enabled tools to builtin directory for runtime initialization
echo ""
echo "Processing volume-enabled tools..."
BUILTIN_DIR="/app/tools-builtin"
mkdir -p "$BUILTIN_DIR"

volume_tools=$(jq -r '.tools | to_entries[] | select(.value.enabled == true and .value.docker_volume == true) | .key' "$CONFIG_FILE")

while IFS= read -r name; do
    if [ -n "$name" ] && [ -d "$TOOLS_DIR/$name" ]; then
        echo "  Moving $name to builtin directory for volume support..."
        mv "$TOOLS_DIR/$name" "$BUILTIN_DIR/$name"
    fi
done <<< "$volume_tools"

echo "Volume-enabled tools prepared for runtime initialization"
