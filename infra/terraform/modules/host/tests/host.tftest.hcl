# Offline: the AWS provider is mocked, so this needs no account. Run with `make tf-check`.
mock_provider "aws" {
  mock_data "aws_caller_identity" {
    defaults = { account_id = "123456789012" }
  }
  mock_data "aws_region" {
    defaults = { region = "us-east-1", name = "us-east-1" }
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
  availability_zone  = "us-east-1a"
  subnet_id          = "subnet-0123456789abcdef0"
  security_group_id  = "sg-0123456789abcdef0"
  backup_bucket_name = "secondmind-backups-123456789012"
  repo_url           = "https://example.test/owner/repo.git"
  tags               = { project = "secondmind", env = "prod" }
}

run "the_host_is_a_t4g_small" {
  command = apply

  assert {
    condition     = aws_instance.host.instance_type == "t4g.small"
    error_message = "the host is a t4g.small"
  }
}

run "the_instance_type_cannot_be_changed_by_accident" {
  command = plan
  variables {
    instance_type = "t4g.medium"
  }
  expect_failures = [var.instance_type]
}

run "imds_v2_is_required_with_a_hop_limit_of_1" {
  command = apply

  assert {
    condition     = one(aws_instance.host.metadata_options).http_tokens == "required"
    error_message = "IMDSv2 must be required"
  }

  assert {
    condition     = one(aws_instance.host.metadata_options).http_put_response_hop_limit == 1
    error_message = "a hop limit of 1 keeps containers away from the instance role's credentials"
  }
}

run "every_volume_is_encrypted" {
  command = apply

  assert {
    condition     = aws_ebs_volume.data.encrypted == true
    error_message = "the data volume must be encrypted"
  }

  assert {
    condition     = one(aws_instance.host.root_block_device).encrypted == true
    error_message = "the root volume must be encrypted"
  }
}

run "the_volumes_are_as_small_as_they_can_safely_be" {
  command = apply

  assert {
    condition     = one(aws_instance.host.root_block_device).volume_size <= 12 && aws_ebs_volume.data.size <= 10
    error_message = "every GB draws credit: about 12 GB and 10 GB"
  }
}

run "the_data_volume_is_snapshotted_daily_and_seven_are_kept" {
  command = apply

  assert {
    condition     = aws_ebs_volume.data.tags["Backup"] == "daily"
    error_message = "the data volume carries the tag the lifecycle policy targets"
  }

  assert {
    condition     = one(aws_dlm_lifecycle_policy.data.policy_details).target_tags["Backup"] == "daily"
    error_message = "the policy targets volumes tagged Backup=daily"
  }

  assert {
    condition     = one(one(one(aws_dlm_lifecycle_policy.data.policy_details).schedule).retain_rule).count == 7
    error_message = "7 snapshots are kept"
  }
}

run "the_host_role_can_do_only_what_the_host_needs" {
  command = apply

  # Every statement, every action, every resource: scoped to this project.
  assert {
    condition = alltrue([
      for s in jsondecode(aws_iam_role_policy.host.policy).Statement :
      alltrue([for r in tolist(s.Resource) : r != "*"])
    ])
    error_message = "no statement may have Resource *"
  }

  assert {
    condition = toset(flatten([
      for s in jsondecode(aws_iam_role_policy.host.policy).Statement : tolist(s.Action)
      ])) == toset([
      "ssm:GetParameter", "ssm:GetParameters", "ssm:GetParametersByPath",
      "kms:Decrypt",
      "s3:PutObject", "s3:AbortMultipartUpload", "s3:ListMultipartUploadParts", "s3:ListBucket",
      "logs:CreateLogStream", "logs:PutLogEvents", "logs:DescribeLogStreams",
    ])
    error_message = "the host role's actions changed: review it against ADR-0035"
  }

  assert {
    condition = alltrue([
      for s in jsondecode(aws_iam_role_policy.host.policy).Statement :
      s.Sid != "ReadThisProjectsParameters" || alltrue([for r in tolist(s.Resource) : startswith(r, "arn:aws:ssm:us-east-1:123456789012:parameter/secondmind/prod")])
    ])
    error_message = "parameters are readable only under /secondmind/prod"
  }

  assert {
    condition = alltrue([
      for s in jsondecode(aws_iam_role_policy.host.policy).Statement :
      s.Sid != "WriteBackups" || alltrue([for r in tolist(s.Resource) : startswith(r, "arn:aws:s3:::secondmind-backups-123456789012/dumps/")])
    ])
    error_message = "backups are writable only under dumps/ in the backup bucket"
  }

  assert {
    condition     = aws_iam_role_policy_attachment.ssm_agent.policy_arn == "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
    error_message = "the SSM agent's own managed policy, and only that one, is attached"
  }
}

run "logs_are_kept_seven_days" {
  command = apply

  assert {
    condition     = aws_cloudwatch_log_group.app.retention_in_days == 7
    error_message = "logs are kept 7 days"
  }
}

run "the_first_boot_script_has_no_secret_in_it" {
  command = apply

  assert {
    condition     = !can(regex("(?i)password|secret|api_key", aws_instance.host.user_data))
    error_message = "user data is readable by anyone with ec2:DescribeInstanceAttribute: no secrets in it"
  }
}
