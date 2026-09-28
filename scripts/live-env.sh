#!/usr/bin/env bash
# Run one command with the repo's .env exported and MODEL_PROVIDER_MODE=live (R.1).
#
# Only the live make targets use this, so provider keys reach live runs and nothing else:
# `make test`, `make test-int` and `make e2e` never go through it. The file is read line by line
# (KEY=value, as docker compose reads it), never sourced, so nothing in it runs as shell.
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ -f "$root/.env" ]]; then
  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ "$line" =~ ^[[:space:]]*(#|$) ]] && continue
    [[ "$line" =~ ^[[:space:]]*(export[[:space:]]+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$ ]] || continue
    key="${BASH_REMATCH[2]}"
    value="${BASH_REMATCH[3]}"
    # Surrounding quotes are part of the syntax, not the value.
    if [[ "$value" =~ ^\"(.*)\"$ || "$value" =~ ^\'(.*)\'$ ]]; then
      value="${BASH_REMATCH[1]}"
    fi
    # A variable already set in the environment wins, as with docker compose.
    if [[ -z "${!key+x}" ]]; then
      export "$key=$value"
    fi
  done < "$root/.env"
fi
export MODEL_PROVIDER_MODE=live
exec "$@"
