#!/usr/bin/env bash
# The AWS half of a backup: copy one dump to the private backup bucket, encrypted, with the
# instance role. Bucket lifecycle expires dumps after 30 days (infra/terraform/app).
set -euo pipefail
CONF="${HOST_CONF:-/etc/secondmind/host.conf}"
# shellcheck source=/dev/null
[[ -f "$CONF" ]] && set -a && . "$CONF" && set +a
file="${1:?usage: upload-backup.sh <dump-file>}"
: "${BACKUP_BUCKET:?BACKUP_BUCKET is not set in $CONF}" "${AWS_REGION:?AWS_REGION is not set}"
aws s3 cp "$file" "s3://$BACKUP_BUCKET/dumps/$(date -u +%Y/%m)/$(basename "$file")" \
  --region "$AWS_REGION" --sse AES256 --only-show-errors
echo "uploaded to s3://$BACKUP_BUCKET/dumps/"
