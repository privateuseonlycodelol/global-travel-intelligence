#!/usr/bin/env bash
# Start PostgreSQL and the travel-ranking MCP server.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

"$ROOT/scripts/start_postgres.sh"
"$ROOT/scripts/start_mcp.sh"
