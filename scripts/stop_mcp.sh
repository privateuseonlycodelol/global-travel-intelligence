#!/usr/bin/env bash
# Stop the travel-ranking MCP server started by start_mcp.sh.
set -euo pipefail

PID_FILE="${MCP_PID:-/tmp/travel_mcp.pid}"

if [[ ! -f "$PID_FILE" ]]; then
  echo "No PID file at $PID_FILE."
  exit 0
fi

PID="$(cat "$PID_FILE")"
if kill -0 "$PID" 2>/dev/null; then
  kill "$PID"
  for _ in {1..20}; do
    kill -0 "$PID" 2>/dev/null || break
    sleep 0.2
  done
  if kill -0 "$PID" 2>/dev/null; then
    kill -9 "$PID" 2>/dev/null || true
  fi
  echo "Stopped MCP server (pid $PID)."
else
  echo "Process $PID is not running."
fi
rm -f "$PID_FILE"
