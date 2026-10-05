#!/usr/bin/env bash
# Run Terraform in a container, so nothing needs installing.
#
#   scripts/tf.sh <root> <terraform args...>      e.g. scripts/tf.sh platform plan
#
# <root> is platform or app (infra/terraform/<root>). `init` adds the S3 backend settings for you
# (the state bucket from `make tf-bootstrap`, a key per root). Your AWS profile (AWS_PROFILE) is
# read from ~/.aws, so the container sees the same login (`aws login`) the AWS CLI does.
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
    -backend-config="region=${AWS_REGION:-ap-southeast-2}"
    "${@:2}")
fi

tty_flag=()
[[ -t 0 && -t 1 ]] && tty_flag=(-it)

# `aws login` credentials are refreshed by writing a token cache, which the read-only ~/.aws mount
# forbids (the apply fails with "failed to refresh cached credentials, save token"). So resolve
# them here, on the host, and hand the container plain temporary credentials (about 15 minutes);
# they go in by name, never on the command line. A profile that can't be exported falls back to
# the mounted profile.
cred_args=(-e "AWS_PROFILE=${AWS_PROFILE:-}")
if creds="$(aws configure export-credentials --format env-no-export 2>/dev/null)"; then
  set -a
  eval "$creds"
  set +a
  cred_args=(-e AWS_ACCESS_KEY_ID -e AWS_SECRET_ACCESS_KEY -e AWS_SESSION_TOKEN)
fi

mkdir -p "$HOME/.cache/terraform-plugins"
exec docker run --rm ${tty_flag[@]+"${tty_flag[@]}"} \
  -v "$root_dir/infra:/infra" \
  -v "$HOME/.aws:/root/.aws:ro" \
  -v "$HOME/.cache/terraform-plugins:/plugin-cache" \
  -e TF_PLUGIN_CACHE_DIR=/plugin-cache \
  "${cred_args[@]}" \
  -e AWS_REGION="${AWS_REGION:-ap-southeast-2}" \
  -e AWS_SDK_LOAD_CONFIG=1 \
  -w "/infra/terraform/$root" "$TF_IMAGE" "${args[@]}"
