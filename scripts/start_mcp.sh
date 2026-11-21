#!/usr/bin/env bash
# Start the travel-ranking MCP server in the background.
# Usage:
#   ./scripts/start_mcp.sh
# Environment overrides:
#   SUPABASE_DB_URL, PORT, MCP_PATH, MCP_LOG, MCP_PID
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DB_URL="${SUPABASE_DB_URL:-postgresql:///travel_global?host=/var/run/postgresql}"
PORT="${PORT:-8000}"
MCP_PATH="${MCP_PATH:-/mcp}"
LOG="${MCP_LOG:-/tmp/travel_mcp.log}"
PID_FILE="${MCP_PID:-/tmp/travel_mcp.pid}"

if [[ -f "$PID_FILE" ]] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  echo "MCP server already running (pid $(cat "$PID_FILE"))."
  exit 0
fi

cd "$ROOT"

if id postgres >/dev/null 2>&1; then
  # Local Postgres uses peer authentication; run the server as the postgres OS
  # user so the Unix-socket URL works without a password.
  su postgres -c "cd '$ROOT' && \
    SUPABASE_DB_URL='$DB_URL' PORT='$PORT' MCP_PATH='$MCP_PATH' \
    nohup .venv/bin/python mcp_server.py > '$LOG' 2>&1 &"
  sleep 2
  PY_PID="$(pgrep -u postgres -n -f '^\.venv/bin/python mcp_server\.py$' || true)"
  if [[ -z "$PY_PID" ]]; then
    echo "MCP server failed to start; last log lines:"
    tail -20 "$LOG" || true
    exit 1
  fi
  echo "$PY_PID" > "$PID_FILE"
else
  SUPABASE_DB_URL="$DB_URL" PORT="$PORT" MCP_PATH="$MCP_PATH" \
    nohup .venv/bin/python mcp_server.py > "$LOG" 2>&1 &
  echo $! > "$PID_FILE"
fi

sleep 2
if ! kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  echo "MCP server failed to start; last log lines:"
  tail -20 "$LOG" || true
  exit 1
fi

echo "MCP server started (pid $(cat "$PID_FILE"))."
echo "Log: $LOG"
curl -fsS "http://127.0.0.1:${PORT}/health" || {
  echo "Health check failed; last log lines:"
  tail -20 "$LOG" || true
  exit 1
}
echo
