#!/usr/bin/env bash
# Check a deployed site from outside, with no model calls: liveness, readiness, that /v1/meta
# reports the expected release, the security headers on both the SPA and the API, and that a
# cross-site POST is refused. Used by the deploy workflow and by `make smoke-prod`.
#
#   scripts/check-deployed.sh https://2nd-mind.<domain> [expected-tag]
#
# Retries for up to 3 minutes: right after the first deploy the certificate is still being issued.
set -euo pipefail
url="${1:?usage: check-deployed.sh <https://host> [expected-tag]}"
url="${url%/}"
tag="${2:-}"
deadline=$((SECONDS + ${CHECK_TIMEOUT_S:-180}))
fail=0

say() { printf '  %-52s %s\n' "$1" "$2"; }
bad() { say "$1" "FAIL: $2"; fail=1; }
ok() { say "$1" "ok"; }

# Wait for the site to answer at all.
until curl -fsS --max-time 8 -o /dev/null "$url/healthz" 2>/dev/null; do
  if ((SECONDS >= deadline)); then echo "no answer from $url/healthz after ${CHECK_TIMEOUT_S:-180}s" >&2; exit 1; fi
  sleep 5
done
echo "checking $url"

[[ "$(curl -fsS --max-time 8 "$url/healthz")" == ok* ]] && ok "/healthz" || bad "/healthz" "not ok"

ready="$(curl -sS --max-time 8 -o /dev/null -w '%{http_code}' "$url/readyz")"
[[ "$ready" == 200 ]] && ok "/readyz answers 200" || bad "/readyz" "status $ready"

meta="$(curl -fsS --max-time 8 "$url/v1/meta")"
version="$(printf '%s' "$meta" | python3 -c 'import json,sys; print(json.load(sys.stdin)["version"])')"
env_name="$(printf '%s' "$meta" | python3 -c 'import json,sys; print(json.load(sys.stdin)["env"])')"
[[ "$env_name" == production ]] && ok "/v1/meta env is production" || bad "/v1/meta" "env is $env_name"
if [[ -n "$tag" ]]; then
  [[ "$version" == "$tag" ]] && ok "/v1/meta reports release $tag" || bad "/v1/meta" "reports $version, expected $tag"
else
  say "/v1/meta reports release" "$version"
fi

header() { curl -sSI --max-time 8 "$1" | tr -d '\r' | grep -i "^$2:" | head -n 1 || true; }
for path in "/" "/v1/meta"; do
  for name in content-security-policy x-content-type-options strict-transport-security x-frame-options; do
    [[ -n "$(header "$url$path" "$name")" ]] && ok "$path sends $name" || bad "$path" "no $name header"
  done
done
[[ "$(header "$url/v1/meta" cache-control)" == *no-store* ]] && ok "/v1/meta is no-store" || bad "/v1/meta" "not no-store"

cross="$(curl -sS --max-time 8 -o /dev/null -w '%{http_code}' -X POST "$url/v1/auth/dev-login" \
  -H 'content-type: application/json' -H 'Sec-Fetch-Site: cross-site' -d '{}')"
[[ "$cross" == 403 ]] && ok "a cross-site POST is refused (403)" || bad "cross-site POST" "status $cross"

if ((fail)); then echo "FAILED" >&2; exit 1; fi
echo "all checks passed"
