#!/usr/bin/env bash
# Check production from outside (`make smoke-prod`): readiness and headers, then the @prodlike E2E
# subset on real models. Prints what it cost and a run id (R.2), and writes a stamped run file.
# Yours to run: the cost comes from the admin CLI over SSM, which needs your AWS profile.
#   PROD_URL=https://2nd-mind.<domain> ACCESS_CODE=... make smoke-prod
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
url="${PROD_URL:-}"
if [[ -z "$url" && -f "$root/.env.prod" ]]; then url="https://$(sed -n 's/^APP_HOST=//p' "$root/.env.prod" | tail -n 1)"; fi
code="${ACCESS_CODE:-}"
if [[ -z "$code" && -f "$root/.env.prod" ]]; then code="$(sed -n 's/^ACCESS_CODE=//p' "$root/.env.prod" | tail -n 1)"; fi
[[ "$url" == https://?* && -n "$code" ]] || { echo "set PROD_URL and ACCESS_CODE, or fill in .env.prod" >&2; exit 2; }

sha="$(git -C "$root" rev-parse --short=12 HEAD)"
run_id="$(date -u +%Y%m%dT%H%M%SZ)-${sha:0:7}"

spend() { "$root/scripts/prod-ssm.sh" admin spend 2>/dev/null | sed -n 's/^today *\$\([0-9.]*\).*/\1/p' | head -n 1; }
before="$(spend || true)"

"$root/scripts/check-deployed.sh" "$url"
status=0
( cd "$root/frontend" && E2E_BASE_URL="$url" E2E_ACCESS_CODE="$code" \
  npx playwright test --grep @prodlike --project=desktop --no-deps ) || status=$?

after="$(spend || true)"
cost="unknown"
if [[ -n "$before" && -n "$after" ]]; then cost="$(python3 -c "print(round(float('$after') - float('$before'), 4))")"; fi
mkdir -p "$root/backend/evals/runs/smoke"
python3 - "$root/backend/evals/runs/smoke/$run_id.prod.json" "$run_id" "$url" "$sha" "$status" "$cost" <<'PY'
import json, sys, datetime
path, run_id, url, sha, status, cost = sys.argv[1:7]
host = url.split("//", 1)[1]
json.dump({"run_id": run_id, "kind": "smoke-prod", "host": host.split("/")[0] and "production",
           "git_sha": sha, "passed": status == "0", "cost_usd": None if cost == "unknown" else float(cost),
           "at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")},
          open(path, "w"), indent=2)
PY
echo "smoke-prod run $run_id: $([[ $status == 0 ]] && echo passed || echo FAILED); cost \$$cost"
exit "$status"
