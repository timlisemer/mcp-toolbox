#!/bin/bash
set -e

CONFIG_FILE="${MCP_SERVERS_CONFIG:-/app/config/servers.json}"
SUPERVISOR_CONF="/etc/supervisor/conf.d/mcp-servers.conf"
SERVERS_DIR="/app/servers"

echo "Generating supervisor configuration for MCP servers..."

# Start with empty supervisor config
echo "# Auto-generated MCP server configurations" > "$SUPERVISOR_CONF"
echo "# Generated at $(date)" >> "$SUPERVISOR_CONF"
echo "" >> "$SUPERVISOR_CONF"

# Parse servers configuration
servers=$(jq -r '.servers | to_entries[] | select(.value.enabled == true) | @json' "$CONFIG_FILE")

server_count=0
while IFS= read -r server_json; do
    server=$(echo "$server_json" | jq -r '.')
    name=$(echo "$server" | jq -r '.key')
    value=$(echo "$server" | jq -r '.value')
    
    type=$(echo "$value" | jq -r '.type')
    binary_path=$(echo "$value" | jq -r '.binary_path')
    install_path=$(echo "$value" | jq -r '.install_path // ""')
    default_args=$(echo "$value" | jq -r '.default_args[]? // ""' | tr '\n' ' ')

    # Extract environment variables from config
    env_vars=$(echo "$value" | jq -r '.environment // {} | to_entries[] | "\(.key)=\(.value)"' | tr '\n' ',' | sed 's/,$//')

    server_dir="$SERVERS_DIR/$name"
    
    # Determine the command based on type and paths
    if [ -n "$install_path" ] && [ "$install_path" != "null" ]; then
        # Use install path if specified, but for Python servers, check if we need venv
        if [ "$type" = "python" ] && [ -f "$server_dir/venv/bin/python" ]; then
            # Replace python3 with venv python path for Python servers
            venv_python="$server_dir/venv/bin/python3"
            command="${install_path/python3/$venv_python}"
        else
            command="$install_path"
        fi
    else
        # Build command based on type
        case "$type" in
            "node")
                # Check custom binary_path first
                if [ -n "$binary_path" ] && [ "$binary_path" != "null" ] && [ -f "$server_dir/$binary_path" ]; then
                    command="node $server_dir/$binary_path"
                elif [ -f "$server_dir/dist/index.js" ]; then
                    command="node $server_dir/dist/index.js"
                elif [ -f "$server_dir/index.js" ]; then
                    command="node $server_dir/index.js"
                elif [ -f "$server_dir/src/index.js" ]; then
                    command="node $server_dir/src/index.js"
                else
                    echo "Warning: No entry point found for $name (binary_path: $binary_path)"
                    continue
                fi
                ;;
            "go")
                if [ -f "$server_dir/$binary_path" ]; then
                    command="$server_dir/$binary_path"
                elif [ -f "/root/go/bin/$(basename $binary_path)" ]; then
                    command="/root/go/bin/$(basename $binary_path)"
                else
                    echo "Warning: Binary not found for $name"
                    continue
                fi
                ;;
            "rust")
                if [ -f "$server_dir/$binary_path" ]; then
                    command="$server_dir/$binary_path"
                else
                    echo "Warning: Binary not found for $name"
                    continue
                fi
                ;;
            "python")
                if [ -f "$server_dir/venv/bin/python" ]; then
                    # Use virtual environment python
                    venv_python="$server_dir/venv/bin/python"
                    if [ -f "$server_dir/main.py" ]; then
                        command="$venv_python $server_dir/main.py"
                    elif [[ "$binary_path" == python* ]]; then
                        # Replace python3 with venv python path
                        command="${binary_path/python3/$venv_python}"
                    else
                        echo "Warning: No entry point found for $name"
                        continue
                    fi
                else
                    echo "Warning: Virtual environment not found for $name"
                    continue
                fi
                ;;
            *)
                echo "Warning: Unknown type $type for $name"
                continue
                ;;
        esac
    fi
    
    # Add default arguments if any
    if [ -n "$default_args" ] && [ "$default_args" != "null" ]; then
        command="$command $default_args"
    fi

    # Build the full environment string before the heredoc
    base_env='PATH="/usr/local/go/bin:/root/go/bin:/root/.cargo/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",GOPATH="/root/go"'
    # Add ANTHROPIC_API_KEY from Docker environment if set
    if [ -n "$ANTHROPIC_API_KEY" ]; then
        base_env="$base_env,ANTHROPIC_API_KEY=\"$ANTHROPIC_API_KEY\""
    fi
    if [ -n "$env_vars" ]; then
        full_env="$base_env,$env_vars"
    else
        full_env="$base_env"
    fi

    # Write supervisor program configuration
    cat >> "$SUPERVISOR_CONF" << ENDCONFIG
[program:mcp-$name]
command=$command
directory=$server_dir
autostart=true
autorestart=true
startretries=3
stderr_logfile=/var/log/mcp/$name.err.log
stdout_logfile=/var/log/mcp/$name.out.log
stdout_logfile_maxbytes=10MB
stderr_logfile_maxbytes=10MB
stdout_logfile_backups=2
stderr_logfile_backups=2
environment=$full_env
user=root

ENDCONFIG
    
    server_count=$((server_count + 1))
    echo "  Configured $name"
done <<< "$servers"

echo ""
echo "Generated supervisor configuration for $server_count servers"
echo "Configuration saved to: $SUPERVISOR_CONF"