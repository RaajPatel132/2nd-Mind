mock_provider "aws" {
  mock_data "aws_caller_identity" {
    defaults = { account_id = "123456789012" }
  }
  mock_data "aws_region" {
    defaults = { region = "us-east-1", name = "us-east-1" }
  }
}

variables {
  github_repo = "owner/repo"
}

run "the_backup_bucket_is_named_for_the_account" {
  command = apply

  assert {
    condition     = output.backup_bucket_name == "secondmind-backups-123456789012"
    error_message = "the host role in the platform root is scoped to this name"
  }
}

run "the_deploy_document_takes_only_a_sha_and_a_migrate_flag" {
  command = apply

  assert {
    condition     = toset(keys(jsondecode(aws_ssm_document.deploy.content).parameters)) == toset(["sha", "migrate"])
    error_message = "the deploy document has two parameters"
  }

  assert {
    condition     = jsondecode(aws_ssm_document.deploy.content).parameters.sha.allowedPattern == "^[0-9a-f]{40}$"
    error_message = "the SHA is validated before it reaches a shell"
  }

  assert {
    condition     = toset(jsondecode(aws_ssm_document.deploy.content).parameters.migrate.allowedValues) == toset(["true", "false"])
    error_message = "migrate is true or false and nothing else"
  }
}

run "no_secret_is_in_the_terraform_of_the_app_root" {
  command = apply

  assert {
    condition     = !can(regex("(?i)password|api_key|secret", aws_ssm_document.deploy.content))
    error_message = "secret values reach SSM by `make prod-secrets`, never through Terraform"
  }
}
