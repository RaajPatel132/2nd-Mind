#!/usr/bin/env bash
# Load a nightly dump into the LOCAL stack (`make restore-local DUMP=<file>`, runbook §7). The
# dump comes from the host (`/var/backups/secondmind/` or the backup bucket). The local stack
# (`make up`) must be running. The database is replaced: run it against a stack you can lose.
set -euo pipefail
dump="${1:?usage: restore-local.sh <dump-file>}"
[[ -f "$dump" ]] || { echo "no such file: $dump" >&2; exit 1; }
compose="${COMPOSE:-docker compose}"

echo "==> stopping the api and worker"
$compose stop api worker >/dev/null 2>&1 || true
echo "==> replacing the database"
$compose exec -T postgres sh -c 'psql -q -U "$POSTGRES_USER" -d postgres \
  -c "DROP DATABASE IF EXISTS \"$POSTGRES_DB\" WITH (FORCE)" -c "CREATE DATABASE \"$POSTGRES_DB\""'
echo "==> the app's database roles"
$compose run --rm --no-deps migrate python -m secondmind.memory.adapters.bootstrap_role
echo "==> restoring $(basename "$dump")"
$compose exec -T postgres sh -c 'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --no-owner --exit-on-error' < "$dump"
echo "==> migrating to this checkout's head (a dump from an older release is brought forward)"
$compose run --rm migrate
$compose up -d --wait api worker
echo "restored. Sign in as the users the dump had; check with: docker compose exec -T postgres psql -U \$POSTGRES_USER -d \$POSTGRES_DB -c 'select count(*) from items'"
