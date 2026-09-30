terraform {
  required_version = ">= 1.10"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.66"
    }
  }

  # The state bucket is made by `make tf-bootstrap`; `make tf-init ROOT=platform` supplies the
  # bucket, key and region. use_lockfile keeps the lock in the bucket itself (no DynamoDB table).
  backend "s3" {
    use_lockfile = true
    encrypt      = true
  }
}
