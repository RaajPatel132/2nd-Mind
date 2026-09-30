# The app root (ADR-0035): 2nd Mind's own pieces on the platform. The backup bucket, the SSM
# document that deploys a release, and the CI roles (GitHub OIDC). The secrets are not here: no
# secret value is ever in Terraform or its state; `make prod-secrets` writes the SSM parameters
# from a local, gitignored file.

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = local.tags
  }
}

data "aws_caller_identity" "current" {}

locals {
  tags = {
    project = "secondmind"
    env     = "prod"
  }
  account_id           = data.aws_caller_identity.current.account_id
  backup_bucket_name   = "secondmind-backups-${local.account_id}"
  state_bucket         = "secondmind-tfstate-${local.account_id}"
  deploy_document_name = "secondmind-deploy"
}

module "backup_bucket" {
  source = "../modules/backup_bucket"
  name   = local.backup_bucket_name
  tags   = local.tags
}

# What a deploy runs on the host, as root: fetch this release's files, then the host's own
# portable deploy script (infra/host/deploy.sh). The role CI assumes may send this document and
# nothing else.
resource "aws_ssm_document" "deploy" {
  name            = local.deploy_document_name
  document_type   = "Command"
  document_format = "JSON"
  tags            = local.tags

  content = jsonencode({
    schemaVersion = "2.2"
    description   = "Deploy one release of 2nd Mind: check out the SHA, then infra/host/deploy.sh."
    parameters = {
      sha = {
        type           = "String"
        description    = "The full git SHA to deploy (40 hex characters)."
        allowedPattern = "^[0-9a-f]{40}$"
      }
      migrate = {
        type          = "String"
        description   = "true runs the migration first; false is a rollback and skips it."
        allowedValues = ["true", "false"]
        default       = "true"
      }
    }
    mainSteps = [{
      action = "aws:runShellScript"
      name   = "deploy"
      inputs = {
        timeoutSeconds = "900"
        runCommand = [
          "set -euo pipefail",
          "cd /opt/secondmind",
          "git fetch --depth 1 origin {{ sha }}",
          "git checkout --force {{ sha }}",
          "exec infra/host/deploy.sh {{ sha }} {{ migrate }}",
        ]
      }
    }]
  })
}

module "ci_access" {
  source               = "../modules/ci_access"
  github_repo          = var.github_repo
  environment_name     = var.github_environment
  deploy_document_name = aws_ssm_document.deploy.name
  state_bucket         = local.state_bucket
  create_oidc_provider = var.create_oidc_provider
  tags                 = local.tags
}
