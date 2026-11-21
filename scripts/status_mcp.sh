#!/usr/bin/env bash
# Show MCP server status and health.
set -euo pipefail

PID_FILE="${MCP_PID:-/tmp/travel_mcp.pid}"
PORT="${PORT:-8000}"

if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  echo "MCP server running (pid $(cat "$PID_FILE"))."
else
  echo "MCP server is not running."
fi

curl -fsS "http://127.0.0.1:${PORT}/health" || true
echo
