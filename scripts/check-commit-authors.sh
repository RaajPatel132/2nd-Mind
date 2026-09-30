#!/usr/bin/env bash
# Fail when a commit in FROM..TO has an author outside the allowlist (R.9).
#
#   COMMIT_AUTHORS="me@example.com,123+me@users.noreply.github.com" \
#     scripts/check-commit-authors.sh <from> <to>
#
# In CI the list is the owner's addresses (in the workflow), or the COMMIT_AUTHORS repository
# variable when it is set, so a commit authored by anyone else (a cloud session committing as
# "Claude", say) can't reach main. An unset list fails closed. Emails compare case-insensitively.
set -euo pipefail
from="${1:?usage: check-commit-authors.sh <from> <to>}"
to="${2:-HEAD}"
if [[ -z "${COMMIT_AUTHORS:-}" ]]; then
  echo "COMMIT_AUTHORS is not set: add it as a repository variable (comma-separated emails)" >&2
  exit 2
fi
allowed=",$(tr '[:upper:]' '[:lower:]' <<<"${COMMIT_AUTHORS// /}"),"
bad=0
while IFS=$'\t' read -r sha name email; do
  [[ -z "$sha" ]] && continue
  lower="$(tr '[:upper:]' '[:lower:]' <<<"$email")"
  if [[ "$allowed" != *",$lower,"* ]]; then
    echo "commit ${sha:0:12} is authored by $name <$email>, who is not in COMMIT_AUTHORS" >&2
    bad=1
  fi
done < <(git log --format=$'%H\t%an\t%ae' "$from..$to")
if [[ $bad -eq 0 ]]; then
  echo "every commit in $from..$to has an allowed author"
fi
exit $bad
