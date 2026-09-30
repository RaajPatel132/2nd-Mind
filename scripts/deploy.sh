#!/usr/bin/env bash
# Trigger the deploy workflow on GitHub (`make deploy`). Deploying is CI's job; this asks it to.
#
#   make deploy                      deploy the head of the current branch (with migrations)
#   make deploy SHA=<sha>            redeploy that release WITHOUT migrations: a rollback, or the
#                                    roll forward again (NFR-10.4: one release of compatibility)
#   make deploy SHA=<sha> MIGRATE=true   a new release that has migrations
#   make deploy REF=<branch>         run the workflow file from another branch
#
# GitHub lists (and lets you dispatch) a workflow only when it is on the default branch, so
# before this sprint is merged use `scripts/prod-ssm.sh deploy <sha>` instead, or push to the
# sprint branch (which deploys after a green run when DEPLOY_SPRINT_BRANCHES is true).
set -euo pipefail
sha="${1:-}"; migrate="${2:-}"; ref="${3:-}"
command -v gh >/dev/null || { echo "install the GitHub CLI (brew install gh) and run gh auth login" >&2; exit 1; }
ref="${ref:-$(git rev-parse --abbrev-ref HEAD)}"
if [[ -n "$sha" ]]; then
  full="$(git rev-parse "$sha^{commit}")"
  migrate="${migrate:-false}"
else
  full="$(git rev-parse "$ref")"
  migrate="${migrate:-true}"
fi
echo "deploying ${full:0:12} (migrate=$migrate) with the workflow from $ref"
gh workflow run deploy.yml --ref "$ref" -f "sha=$full" -f "migrate=$migrate"
echo "started. Watch it:  gh run watch \$(gh run list --workflow deploy.yml --limit 1 --json databaseId --jq '.[0].databaseId')"
