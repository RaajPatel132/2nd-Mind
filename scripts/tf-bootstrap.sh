#!/usr/bin/env bash
# Create the Terraform state bucket: the one AWS resource that isn't made by Terraform, because
# Terraform needs somewhere to keep its state before it can make anything.
# Idempotent. Yours to run, with your AWS profile:  AWS_PROFILE=second-mind make tf-bootstrap
set -euo pipefail
region="${AWS_REGION:-ap-southeast-2}"
account="$(aws sts get-caller-identity --query Account --output text)"
bucket="secondmind-tfstate-$account"

if aws s3api head-bucket --bucket "$bucket" 2>/dev/null; then
  echo "bucket $bucket already exists"
else
  echo "creating $bucket in $region"
  # Every region except us-east-1 needs its name as a LocationConstraint.
  location=()
  [[ "$region" == "us-east-1" ]] || location=(--create-bucket-configuration "LocationConstraint=$region")
  aws s3api create-bucket --bucket "$bucket" --region "$region" ${location[@]+"${location[@]}"} >/dev/null
fi
aws s3api put-bucket-versioning --bucket "$bucket" --versioning-configuration Status=Enabled
aws s3api put-bucket-encryption --bucket "$bucket" --server-side-encryption-configuration \
  '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}'
aws s3api put-public-access-block --bucket "$bucket" --public-access-block-configuration \
  BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
aws s3api put-bucket-tagging --bucket "$bucket" \
  --tagging 'TagSet=[{Key=project,Value=secondmind},{Key=env,Value=prod}]'
echo "state bucket ready: s3://$bucket (versioned, encrypted, public access blocked)"
echo "next: make tf-init ROOT=platform"
