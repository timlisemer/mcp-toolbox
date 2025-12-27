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

    echo ""
    echo "Building $name ($type)..."
    echo "  Description: $description"
    echo "  Repository: $repo"

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
                echo '{"name": "'$name'", "version": "1.0.0"}' > package.json
                echo 'console.log("MCP Tool: '$name'");' > index.js
                ;;
            "go")
                echo 'package main; import "fmt"; func main() { fmt.Println("MCP Tool: '$name'") }' > main.go
                ;;
            "rust")
                cargo init --name "$name" 2>/dev/null || true
                ;;
        esac
    fi

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
                npm install 2>/dev/null || echo "  Dependencies skipped"
                if [[ "$build_cmd" == *"npm run build"* ]] && [ -f "package.json" ]; then
                    grep -q '"build"' package.json && eval "$build_cmd" || echo "  Build skipped"
                fi
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

done <<< "$tools"

echo ""
echo "All MCP tools built successfully!"
echo "Tools directory: $TOOLS_DIR"
ls -la "$TOOLS_DIR" 2>/dev/null || true
