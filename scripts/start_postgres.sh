#!/usr/bin/env bash
# Start the local PostgreSQL 15 cluster used by this workspace.
set -euo pipefail

PGDATA="${PGDATA:-/var/lib/postgresql/15/main}"
PGLOG="${PGLOG:-/tmp/pg15.log}"

if su postgres -c "pg_isready -q"; then
  echo "PostgreSQL already accepting connections."
  exit 0
fi

mkdir -p /var/run/postgresql
chown postgres:postgres /var/run/postgresql
su postgres -c "/usr/lib/postgresql/15/bin/pg_ctl -D '$PGDATA' -l '$PGLOG' start"
sleep 1
su postgres -c "pg_isready"
