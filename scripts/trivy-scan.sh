#!/usr/bin/env bash
# Scan images with Trivy; fail on CRITICAL vulnerabilities that have a fix available.
# Uses a local `trivy` binary when present, otherwise the official container image.
set -euo pipefail
TRIVY_IMAGE="aquasec/trivy:0.74.0"
status=0
for image in "$@"; do
  echo "==> trivy: $image"
  args=(image --severity CRITICAL --ignore-unfixed --exit-code 1 --no-progress "$image")
  if command -v trivy >/dev/null 2>&1; then
    trivy "${args[@]}" || status=1
  else
    docker run --rm -v /var/run/docker.sock:/var/run/docker.sock \
      -v "${HOME}/.cache/trivy:/root/.cache/trivy" "$TRIVY_IMAGE" "${args[@]}" || status=1
  fi
done
exit $status
