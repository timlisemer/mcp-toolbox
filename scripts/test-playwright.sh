#!/usr/bin/env bash
set -euo pipefail

container_name="${MCP_TOOLBOX_CONTAINER:-mcp-toolbox}"
test_url="${PLAYWRIGHT_TEST_URL:-https://example.com}"

initialize_request='{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"mcp-toolbox-test","version":"1.0"}}}'
initialized_notification='{"jsonrpc":"2.0","method":"notifications/initialized","params":{}}'
navigate_request="$(jq -cn --arg url "$test_url" '{jsonrpc:"2.0",id:2,method:"tools/call",params:{name:"browser_navigate",arguments:{url:$url}}}')"

response="$({
    printf '%s\n' "$initialize_request"
    sleep 1
    printf '%s\n' "$initialized_notification"
    printf '%s\n' "$navigate_request"
    sleep 15
} | timeout 30 docker exec -i "$container_name" \
    /run/current-system/sw/bin/playwright-mcp \
    --headless \
    --browser chromium \
    --no-sandbox \
    --output-mode stdout)"

if ! printf '%s\n' "$response" | jq -e --arg url "$test_url" '
    select(
        .id == 2
        and (.result.content | any(
            .type == "text"
            and (.text | contains($url))
        ))
    )
' >/dev/null; then
    echo "Error: Playwright did not navigate to $test_url" >&2
    printf '%s\n' "$response" >&2
    exit 1
fi

echo "playwright: opened $test_url"
