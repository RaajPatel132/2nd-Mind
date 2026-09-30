#!/usr/bin/env bash
# Write production's parameters to SSM Parameter Store from the gitignored .env.prod (guide step
# 10). Secrets become SecureString parameters, the rest String, all under /secondmind/prod/.
# .env.prod.example says which is which and which names exist: a name it doesn't list is refused
# (a typo would otherwise be written and never read), and so is an empty secret.
#
# Yours to run, with your AWS profile: the session never holds AWS credentials.
#   AWS_PROFILE=secondmind make prod-secrets
#   PROD_SECRETS_DRY_RUN=1 make prod-secrets     # check the file, write nothing
#
# Plain bash 3.2 (macOS's own): no associative arrays.
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
example="${PROD_SECRETS_EXAMPLE:-$root/.env.prod.example}"
file="${PROD_SECRETS_FILE:-$root/.env.prod}"
path="${SSM_PATH:-/secondmind/prod}"
region="${AWS_REGION:-us-east-1}"
dry="${PROD_SECRETS_DRY_RUN:-}"

[[ -f "$file" ]] || { echo "no $file: copy .env.prod.example to .env.prod and fill it in" >&2; exit 2; }

# The names in each section of the example, one per line.
names_in() { awk -v want="$1" '
  /^# \[secret\]/ { sec = "secret"; next }
  /^# \[plain\]/  { sec = "plain"; next }
  /^[A-Z][A-Z0-9_]*=/ { if (sec == want) { sub(/=.*/, ""); print } }' "$example"; }
secret_names="$(names_in secret)"
plain_names="$(names_in plain)"

parsed="$(mktemp)"; trap 'rm -f "$parsed"' EXIT
problems=0
while IFS= read -r line || [[ -n "$line" ]]; do
  [[ "$line" =~ ^[[:space:]]*(#|$) ]] && continue
  if [[ ! "$line" =~ ^([A-Z][A-Z0-9_]*)=(.*)$ ]]; then
    echo "not a NAME=value line: ${line%%=*}" >&2; problems=$((problems + 1)); continue
  fi
  name="${BASH_REMATCH[1]}"; val="${BASH_REMATCH[2]}"
  if ! printf '%s\n%s\n' "$secret_names" "$plain_names" | grep -qx "$name"; then
    echo "$name is not in .env.prod.example: a typo, or add it there and to docs/deploy/config.md" >&2
    problems=$((problems + 1)); continue
  fi
  if [[ "$val" == *"'"* ]]; then
    echo "$name: a value may not contain a single quote" >&2; problems=$((problems + 1)); continue
  fi
  printf '%s\t%s\n' "$name" "$val" >> "$parsed"
done < "$file"

value_of() { awk -F'\t' -v n="$1" '$1 == n { print substr($0, length(n) + 2) }' "$parsed" | tail -n 1; }

for name in $secret_names; do
  [[ -n "$(value_of "$name")" ]] || { echo "$name is a secret and has no value" >&2; problems=$((problems + 1)); }
done
[[ "$(value_of APP_HOST)" == *.* ]] || { echo "APP_HOST should be 2nd-mind.<domain>" >&2; problems=$((problems + 1)); }
if ((problems)); then echo "$problems problem(s): nothing was written" >&2; exit 2; fi

written=0
for name in $(printf '%s\n%s\n' "$secret_names" "$plain_names" | sort); do
  val="$(value_of "$name")"
  [[ -n "$val" ]] || continue                 # an empty plain value is left unset, not written
  type=String
  printf '%s\n' "$secret_names" | grep -qx "$name" && type=SecureString
  if [[ -n "$dry" ]]; then
    printf '  would write %-34s %s\n' "$path/$name" "$type"
  else
    tmp="$(mktemp)"
    printf '%s' "$val" > "$tmp"               # a file, so the value never shows in `ps`
    aws ssm put-parameter --region "$region" --name "$path/$name" --type "$type" \
      --value "file://$tmp" --overwrite --no-cli-pager >/dev/null
    aws ssm add-tags-to-resource --region "$region" --resource-type Parameter \
      --resource-id "$path/$name" --tags Key=project,Value=secondmind Key=env,Value=prod
    rm -f "$tmp"
    printf '  wrote %-34s %s\n' "$path/$name" "$type"
  fi
  written=$((written + 1))
done
echo "$written parameter(s) $([[ -n "$dry" ]] && echo checked || echo written) under $path/"
