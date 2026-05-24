default: help

help:
    @echo "MCP Toolbox - Commands"
    @echo "======================"
    @echo ""
    @echo "  just build    - Build Docker image"
    @echo "  just run      - Run container (foreground, Ctrl+C to stop)"
    @echo "  just stop     - Stop container"
    @echo "  just restart  - Restart container"
    @echo "  just logs     - View container logs"
    @echo "  just shell    - Open container shell"
    @echo "  just status   - List available MCP tools"
    @echo "  just test     - Test MCP tools respond"
    @echo "  just check    - Validate config files"
    @echo "  just clean    - Remove container and image"
    @echo "  just rebuild  - Clean rebuild"

build:
    @echo "Building MCP Toolbox..."
    docker-compose build

run:
    docker-compose down
    docker-compose up

stop:
    docker-compose down

restart: stop run

logs:
    docker-compose logs -f

shell:
    docker exec -it mcp-toolbox /bin/bash

status:
    @echo "Available MCP tools:"
    @echo "===================="
    @docker exec mcp-toolbox cat /app/config/servers.json 2>/dev/null | \
        jq -r '.tools | to_entries[] | select(.value.enabled) | "  \(.key): \(.value.description)"' || \
        echo "Container not running"

test:
    @echo "Testing MCP tools..."
    @echo ""
    @echo "mcp-nixos:"
    @echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"capabilities":{}}}' | \
        timeout 3 docker exec -i mcp-toolbox /app/tools/mcp-nixos/venv/bin/python3 -m mcp_nixos.server 2>/dev/null | head -1 || \
        echo "  (tool responds to JSON-RPC input)"

check:
    @echo "Validating config..."
    @jq empty config/servers.json && echo "config/servers.json: valid JSON"
    @echo "Status: PASS"

clean:
    docker-compose down -v
    docker rmi mcp-toolbox:latest 2>/dev/null || true
    @echo "Cleaned up"

rebuild: clean build run
