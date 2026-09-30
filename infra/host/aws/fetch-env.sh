#!/usr/bin/env bash
# The AWS half of a deploy: print the app's environment file (KEY='value' lines) from SSM
# Parameter Store, using the instance role. Run by deploy.sh as root, on the host, never in a
# container. Every parameter under SSM_PATH becomes one variable, named by the last path segment.
#
# Values are single-quoted, which compose reads literally. `make prod-secrets` refuses a value
# with a quote or a newline in it, so nothing needs escaping.
set -euo pipefail
CONF="${HOST_CONF:-/etc/secondmind/host.conf}"
# shellcheck source=/dev/null
[[ -f "$CONF" ]] && set -a && . "$CONF" && set +a
: "${SSM_PATH:?SSM_PATH is not set in $CONF}" "${AWS_REGION:?AWS_REGION is not set in $CONF}"

aws ssm get-parameters-by-path --region "$AWS_REGION" --path "${SSM_PATH%/}/" \
  --with-decryption --recursive --output json \
  | jq -r '.Parameters[] | (.Name | split("/") | last) as $k
           | select($k | test("^[A-Z][A-Z0-9_]*$")) | "\($k)=\(.Value | @sh)"'
