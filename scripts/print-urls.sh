#!/usr/bin/env bash
# Print where everything is after `make up`.
set -euo pipefail
web="http://localhost:${WEB_PORT:-8080}"
api="http://localhost:${API_PORT:-8000}"
lf="http://localhost:${LANGFUSE_PORT:-3000}"
mode=$(curl -fsS "$api/v1/meta" 2>/dev/null | python3 -c 'import sys,json; m=json.load(sys.stdin); print(m["provider_mode"], "| answer step:", next(r["provider"]+":"+r["model"] for r in m["routes"] if r["step"]=="answer"))' 2>/dev/null || echo "unknown")
cat <<TXT

  2nd Mind is up.
    Web app      $web
    API          $api   (docs: $api/docs/api, readiness: $api/readyz)
    Langfuse     $lf    (login: ${LANGFUSE_INIT_USER_EMAIL:-dev@example.com} / ${LANGFUSE_INIT_USER_PASSWORD:-local-dev-password})
    Providers    $mode

TXT
