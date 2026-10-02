#!/usr/bin/env bash
# Fill in the repository's About box on GitHub: the description, the topics, the website and
# private vulnerability reporting (which SECURITY.md points people at).
#
#   scripts/set-repo-about.sh                 print what would be sent, change nothing
#   GITHUB_TOKEN=... scripts/set-repo-about.sh --apply
#   HOMEPAGE=https://2nd-mind.example.org REPO=owner/name scripts/set-repo-about.sh --apply
#
# The token is yours: a fine-grained token limited to this one repository, with "Administration:
# Read and write". It is read from the environment and never written anywhere. Two things GitHub
# only lets you do in the web UI: upload the social preview card (Settings > General > Social
# preview, docs/media/social-preview.png) and pin the repository on your profile.
set -euo pipefail

repo="${REPO:-RaajPatel132/2nd-Mind}"
homepage="${HOMEPAGE:-}"
description="A personal memory that shows its work: save anything in plain words, find it by meaning, time or person, and open every step."
# Lower case, hyphens, at most 50 characters each and 20 in all (GitHub's rule).
topics=(llm rag ai-agents memory fastapi langgraph pgvector postgres react typescript evals row-level-security)

apply=false
[[ "${1:-}" == "--apply" ]] && apply=true

payload="$(python3 - "$description" "$homepage" <<'PY'
import json, sys
body = {"description": sys.argv[1]}
if sys.argv[2]:
    body["homepage"] = sys.argv[2]
print(json.dumps(body))
PY
)"
topic_payload="$(python3 -c 'import json,sys; print(json.dumps({"names": sys.argv[1:]}))' "${topics[@]}")"

echo "repository: $repo"
echo "PATCH /repos/$repo            $payload"
echo "PUT   /repos/$repo/topics     $topic_payload"
echo "PUT   /repos/$repo/private-vulnerability-reporting"
if ! $apply; then
  echo "(dry run: nothing was sent; add --apply with GITHUB_TOKEN set)"
  exit 0
fi

: "${GITHUB_TOKEN:?set GITHUB_TOKEN to a token for this repository}"
api() {
  local method="$1" path="$2" data="${3:-}"
  curl -fsS -X "$method" "https://api.github.com$path" \
    -H "Authorization: Bearer $GITHUB_TOKEN" \
    -H "Accept: application/vnd.github+json" \
    -H "X-GitHub-Api-Version: 2022-11-28" \
    ${data:+-d "$data"} >/dev/null
}
api PATCH "/repos/$repo" "$payload"
api PUT "/repos/$repo/topics" "$topic_payload"
api PUT "/repos/$repo/private-vulnerability-reporting"
echo "done: the About box, the topics and private vulnerability reporting are set on $repo"
