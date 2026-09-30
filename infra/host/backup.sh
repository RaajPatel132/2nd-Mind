#!/usr/bin/env bash
# Nightly logical backup (ADR-0035): pg_dump in the database container, kept on the host for two
# days, and uploaded by the host (containers hold no cloud credentials). Restore: `make
# restore-local DUMP=<file>` loads a dump into the local stack; runbook §7.
set -euo pipefail

CONF="${HOST_CONF:-/etc/secondmind/host.conf}"
# shellcheck source=/dev/null
[[ -f "$CONF" ]] && set -a && . "$CONF" && set +a
APP_DIR="${APP_DIR:-/opt/secondmind}"
ENV_FILE="${ENV_FILE:-/etc/secondmind/prod.env}"
OUT_DIR="${BACKUP_DIR:-/var/backups/secondmind}"
BACKUP_UPLOAD="${BACKUP_UPLOAD:-none}"
TAG="$(cat /var/lib/secondmind/current 2>/dev/null || echo none)"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
file="$OUT_DIR/secondmind-$stamp.dump"

mkdir -p "$OUT_DIR"
umask 077
IMAGE_TAG="$TAG" \
  docker compose -p secondmind -f "$APP_DIR/compose.prodlike.yaml" --env-file "$ENV_FILE" \
  exec -T postgres sh -c 'pg_dump -U "$POSTGRES_USER" -Fc "$POSTGRES_DB"' > "$file.part"
mv "$file.part" "$file"
echo "dumped $(du -h "$file" | cut -f1) to $file"

case "$BACKUP_UPLOAD" in
  aws) "$APP_DIR/infra/host/aws/upload-backup.sh" "$file" ;;
  none) echo "BACKUP_UPLOAD=none: kept on the host only" ;;
  *) echo "unknown BACKUP_UPLOAD=$BACKUP_UPLOAD" >&2; exit 1 ;;
esac
find "$OUT_DIR" -name 'secondmind-*.dump' -mtime +2 -delete
