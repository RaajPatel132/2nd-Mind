#!/usr/bin/env bash
# Deploy one release on this host (ADR-0035). Portable: the only AWS part is where the env file
# comes from (aws/fetch-env.sh); ENV_SOURCE=file uses one already in place.
#
#   deploy.sh <git-sha> [--no-migrate]
#
# In order: fetch the env file, pull the images by SHA, run the migration as a one-off, bring the
# stack up on the new tag, wait until /readyz answers from this release. If the migration fails
# nothing has changed and the old release keeps serving. If readiness doesn't come, the previous
# tag is put back. `--no-migrate` (a rollback) skips the migration: one release of schema
# compatibility makes that safe (NFR-10.4).
#
# Settings live in /etc/secondmind/host.conf (KEY=value): ENV_SOURCE (ssm|file), ENV_FILE, and
# for ssm: SSM_PATH and AWS_REGION. The env file holds the app's values, IMAGE_REGISTRY included.
set -euo pipefail

SHA="${1:?usage: deploy.sh <git-sha> [--no-migrate]}"
MIGRATE=1
[[ "${2:-}" == "--no-migrate" || "${2:-}" == "false" ]] && MIGRATE=0

APP_DIR="${APP_DIR:-/opt/secondmind}"
CONF="${HOST_CONF:-/etc/secondmind/host.conf}"
STATE_DIR="${STATE_DIR:-/var/lib/secondmind}"
# shellcheck source=/dev/null
[[ -f "$CONF" ]] && set -a && . "$CONF" && set +a
ENV_SOURCE="${ENV_SOURCE:-ssm}"
ENV_FILE="${ENV_FILE:-/etc/secondmind/prod.env}"
READY_TIMEOUT_S="${READY_TIMEOUT_S:-150}"
TAG="${SHA:0:12}"
PREVIOUS="$(cat "$STATE_DIR/current" 2>/dev/null || true)"

log() { echo "==> $(date -u +%H:%M:%S) $*"; }

# IMAGE_REGISTRY (ghcr.io/<owner>) is in the env file; only the tag changes per call.
compose() {
  IMAGE_TAG="$1" docker compose -p secondmind -f "$APP_DIR/compose.prodlike.yaml" \
    --env-file "$ENV_FILE" "${@:2}"
}

wait_ready() { # wait_ready TAG: /readyz answers 200 and /v1/meta reports this release
  local tag="$1" deadline=$((SECONDS + READY_TIMEOUT_S))
  while ((SECONDS < deadline)); do
    if compose "$tag" exec -T web wget -q -O /dev/null http://127.0.0.1:8080/readyz 2>/dev/null \
      && compose "$tag" exec -T web wget -q -O - http://127.0.0.1:8080/v1/meta 2>/dev/null \
        | grep -q "\"version\":\"$tag\""; then
      return 0
    fi
    sleep 3
  done
  return 1
}

# ------------------------------------------------------------------ the env file
log "release $TAG (previous: ${PREVIOUS:-none}); migrate=$MIGRATE"
if [[ "$ENV_SOURCE" == "ssm" ]]; then
  umask 077
  "$APP_DIR/infra/host/aws/fetch-env.sh" > "$ENV_FILE.new"
  mv "$ENV_FILE.new" "$ENV_FILE"
fi
[[ -s "$ENV_FILE" ]] || { echo "no env file at $ENV_FILE" >&2; exit 1; }
chmod 600 "$ENV_FILE"

# ------------------------------------------------------------------ images and the data services
log "pull"
compose "$TAG" pull api web caddy postgres redis
compose "$TAG" up -d --no-deps --wait postgres redis

# ------------------------------------------------------------------ migrate, as a one-off
if ((MIGRATE)); then
  log "migrate"
  if ! compose "$TAG" run --rm --no-deps migrate; then
    echo "migration failed: nothing was changed, ${PREVIOUS:-the previous release} keeps serving" >&2
    exit 1
  fi
fi

# ------------------------------------------------------------------ bring the stack up
log "up"
compose "$TAG" up -d --no-deps api worker web caddy
if ! wait_ready "$TAG"; then
  echo "release $TAG did not become ready in ${READY_TIMEOUT_S}s" >&2
  compose "$TAG" logs --tail 40 api >&2 || true
  if [[ -n "$PREVIOUS" && "$PREVIOUS" != "$TAG" ]]; then
    log "putting $PREVIOUS back"
    compose "$PREVIOUS" up -d --no-deps api worker web caddy
    wait_ready "$PREVIOUS" && log "$PREVIOUS is serving again" || echo "$PREVIOUS is not ready either" >&2
  fi
  exit 1
fi

# ------------------------------------------------------------------ record and tidy
mkdir -p "$STATE_DIR"
[[ "$PREVIOUS" != "$TAG" && -n "$PREVIOUS" ]] && echo "$PREVIOUS" > "$STATE_DIR/previous"
echo "$TAG" > "$STATE_DIR/current"
# Keep this release and the one before it; anything older is re-pulled if a rollback needs it.
keep_a="$TAG"
keep_b="$(cat "$STATE_DIR/previous" 2>/dev/null || echo "$TAG")"
docker images --format '{{.Repository}}:{{.Tag}}' | grep -E '/secondmind-(api|web):|^secondmind-(api|web):' \
  | grep -v -e ":$keep_a\$" -e ":$keep_b\$" | xargs -r docker rmi >/dev/null 2>&1 || true
docker image prune -f >/dev/null 2>&1 || true
log "release $TAG is serving"
