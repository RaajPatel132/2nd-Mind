#!/usr/bin/env bash
# Run Terraform in a container, so nothing needs installing (guide step 4).
#
#   scripts/tf.sh <root> <terraform args...>      e.g. scripts/tf.sh platform plan
#
# <root> is platform or app (infra/terraform/<root>). `init` adds the S3 backend settings for you
# (the state bucket from `make tf-bootstrap`, a key per root). Your AWS profile (AWS_PROFILE) is
# read from ~/.aws, so the container sees the same SSO login the AWS CLI does.
set -euo pipefail
root_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TF_IMAGE="${TF_IMAGE:-hashicorp/terraform:1.16.4}"
root="${1:?usage: tf.sh <platform|app> <terraform args...>}"
shift
[[ -d "$root_dir/infra/terraform/$root" ]] || { echo "no such root: $root (platform or app)" >&2; exit 2; }

args=("$@")
if [[ "${1:-}" == "init" ]]; then
  account="$(aws sts get-caller-identity --query Account --output text)"
  args=(init -input=false
    -backend-config="bucket=secondmind-tfstate-$account"
    -backend-config="key=$root/terraform.tfstate"
    -backend-config="region=${AWS_REGION:-us-east-1}"
    "${@:2}")
fi

tty_flag=()
[[ -t 0 && -t 1 ]] && tty_flag=(-it)
mkdir -p "$HOME/.cache/terraform-plugins"
exec docker run --rm ${tty_flag[@]+"${tty_flag[@]}"} \
  -v "$root_dir/infra:/infra" \
  -v "$HOME/.aws:/root/.aws:ro" \
  -v "$HOME/.cache/terraform-plugins:/plugin-cache" \
  -e TF_PLUGIN_CACHE_DIR=/plugin-cache \
  -e AWS_PROFILE="${AWS_PROFILE:-}" \
  -e AWS_REGION="${AWS_REGION:-us-east-1}" \
  -e AWS_SDK_LOAD_CONFIG=1 \
  -w "/infra/terraform/$root" "$TF_IMAGE" "${args[@]}"
