mock_provider "aws" {}

variables {
  name = "secondmind-backups-123456789012"
  tags = { project = "secondmind", env = "prod" }
}

run "the_bucket_is_private_encrypted_and_versioned" {
  command = apply

  assert {
    condition = alltrue([
      aws_s3_bucket_public_access_block.this.block_public_acls,
      aws_s3_bucket_public_access_block.this.block_public_policy,
      aws_s3_bucket_public_access_block.this.ignore_public_acls,
      aws_s3_bucket_public_access_block.this.restrict_public_buckets,
    ])
    error_message = "public access must be blocked in every way"
  }

  assert {
    condition     = one(one(aws_s3_bucket_server_side_encryption_configuration.this.rule).apply_server_side_encryption_by_default).sse_algorithm == "AES256"
    error_message = "the bucket must be encrypted at rest"
  }

  assert {
    condition     = one(aws_s3_bucket_versioning.this.versioning_configuration).status == "Enabled"
    error_message = "the bucket must be versioned"
  }
}

run "dumps_expire_after_30_days" {
  command = apply

  assert {
    condition     = one(one(aws_s3_bucket_lifecycle_configuration.this.rule).expiration).days == 30
    error_message = "a dump expires after 30 days"
  }
}

run "only_encrypted_transport_is_allowed" {
  command = apply

  assert {
    condition     = jsondecode(aws_s3_bucket_policy.tls_only.policy).Statement[0].Condition.Bool["aws:SecureTransport"] == "false"
    error_message = "the policy denies requests that don't use TLS"
  }
}
