#!/usr/bin/env bash
# Fail when a commit in FROM..TO has an author outside the allowlist (R.9).
#
#   COMMIT_AUTHORS="me@example.com,123+me@users.noreply.github.com" \
#     scripts/check-commit-authors.sh <from> <to>
#
# In CI the list is the owner's addresses (in the workflow), or the COMMIT_AUTHORS repository
# variable when it is set, so a commit authored by anyone else (a cloud session committing as
# "Claude", say) can't reach main. An unset list fails closed. Emails compare case-insensitively.
#
# The range is checked before it is used, and a check that could not read it never passes: a
# <from> that is not a commit here (a force-push leaves the old tip out of the clone, an empty
# value) falls back to the merge base with main, so every commit of the push is looked at, not just
# the last. With no merge base to fall back on it exits 2.
set -euo pipefail
from="${1-}"
to="${2:-HEAD}"
if [[ -z "${COMMIT_AUTHORS:-}" ]]; then
  echo "COMMIT_AUTHORS is not set: add it as a repository variable (comma-separated emails)" >&2
  exit 2
fi

is_commit() { [[ -n "${1-}" ]] && git rev-parse --verify --quiet "$1^{commit}" >/dev/null 2>&1; }

if ! is_commit "$to"; then
  echo "cannot read '$to' as a commit (is this a git checkout?): no author check was done" >&2
  exit 2
fi
if ! is_commit "$from"; then
  base=""
  for main in origin/main main; do
    if is_commit "$main"; then
      base="$(git merge-base "$main" "$to" 2>/dev/null || true)"
      [[ -n "$base" ]] && break
    fi
  done
  if [[ -z "$base" ]]; then
    echo "'$from' is not a commit here and there is no merge base with main to fall back on: no author check was done" >&2
    exit 2
  fi
  if [[ "$base" == "$(git rev-parse "$to^{commit}")" ]]; then
    # Already on main: all that can be said is what the last commit is.
    base="$(git rev-parse --verify --quiet "$to~1" || true)"
    [[ -n "$base" ]] || { echo "nothing before '$to' to check against: no author check was done" >&2; exit 2; }
  fi
  echo "'$from' is not a commit here; checking every commit since the merge base with main (${base:0:12})" >&2
  from="$base"
fi

allowed=",$(tr '[:upper:]' '[:lower:]' <<<"${COMMIT_AUTHORS// /}"),"
# Read into a variable, not through a process substitution: a failing git must stop the script.
commits="$(git log --format=$'%H\t%an\t%ae' "$from..$to")"
bad=0
while IFS=$'\t' read -r sha name email; do
  [[ -z "$sha" ]] && continue
  lower="$(tr '[:upper:]' '[:lower:]' <<<"$email")"
  if [[ "$allowed" != *",$lower,"* ]]; then
    echo "commit ${sha:0:12} is authored by $name <$email>, who is not in COMMIT_AUTHORS" >&2
    bad=1
  fi
done <<<"$commits"
if [[ $bad -eq 0 ]]; then
  echo "every commit in $from..$to has an allowed author"
fi
exit $bad
