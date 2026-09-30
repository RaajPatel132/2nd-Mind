# The wiring of the whole platform root, planned offline with the AWS provider mocked.
mock_provider "aws" {
  mock_data "aws_caller_identity" {
    defaults = { account_id = "123456789012" }
  }
  mock_data "aws_region" {
    defaults = { region = "us-east-1", name = "us-east-1" }
  }
  mock_data "aws_availability_zones" {
    defaults = { names = ["us-east-1a", "us-east-1b"] }
  }
  mock_data "aws_kms_alias" {
    defaults = { target_key_arn = "arn:aws:kms:us-east-1:123456789012:key/00000000-0000-0000-0000-000000000000" }
  }
  mock_resource "aws_iam_role" {
    defaults = { arn = "arn:aws:iam::123456789012:role/mock" }
  }
  mock_resource "aws_cloudwatch_log_group" {
    defaults = { arn = "arn:aws:logs:us-east-1:123456789012:log-group:/secondmind/prod" }
  }
  mock_resource "aws_ebs_volume" {
    defaults = { id = "vol-0123456789abcdef0" }
  }
  mock_resource "aws_instance" {
    defaults = { id = "i-0123456789abcdef0" }
  }
  mock_data "aws_ssm_parameter" {
    defaults = { value = "ami-0123456789abcdef0" }
  }
}

variables {
  domain_name = "example.test"
  alert_email = "owner@example.test"
  github_repo = "owner/repo"
}

run "the_app_is_reached_at_a_name_built_from_the_variables" {
  command = apply

  assert {
    condition     = output.app_hostname == "2nd-mind.example.test"
    error_message = "the host name is <app_subdomain>.<domain_name>"
  }

  assert {
    condition     = can(regex("^A  2nd-mind.example.test  ->  ", output.dns_record))
    error_message = "the dns_record output names the one record to add"
  }
}

run "the_backup_bucket_name_matches_the_one_the_app_root_makes" {
  command = apply

  assert {
    condition     = output.backup_bucket_name == "secondmind-backups-123456789012"
    error_message = "platform and app must agree on the backup bucket's name"
  }
}

run "a_domain_with_a_scheme_is_refused" {
  command = plan
  variables {
    domain_name = "https://example.test"
  }
  expect_failures = [var.domain_name]
}
