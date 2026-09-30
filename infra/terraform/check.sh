#!/bin/sh
# Format, validate and test every Terraform root and module, offline: providers are mocked in the
# tests, so no AWS account or credentials are involved. Runs inside the Terraform image
# (`make tf-check`), and the same in CI.
set -eu
cd /infra/terraform
status=0

# The modules are throwaway test roots with no lock file of their own, so let them install the
# provider from the shared cache; the two roots' committed lock files are what pins it.
export TF_PLUGIN_CACHE_MAY_BREAK_DEPENDENCY_LOCK_FILE=true

echo "==> terraform fmt"
terraform fmt -check -recursive -diff || status=1

for dir in modules/network modules/host modules/budgets modules/backup_bucket modules/ci_access platform app; do
  echo "==> $dir"
  ( cd "$dir"
    terraform init -backend=false -input=false -no-color >/dev/null
    terraform validate -no-color
    if [ -d tests ]; then terraform test -no-color; fi ) || status=1
done
exit $status
