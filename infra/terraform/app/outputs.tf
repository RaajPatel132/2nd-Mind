output "deploy_role_arn" {
  description = "Set as the AWS_DEPLOY_ROLE_ARN repository variable."
  value       = module.ci_access.deploy_role_arn
}

output "plan_role_arn" {
  description = "Set as the AWS_PLAN_ROLE_ARN repository variable, to get a plan on pull requests."
  value       = module.ci_access.plan_role_arn
}

output "backup_bucket_name" {
  value = module.backup_bucket.bucket_name
}

output "deploy_document_name" {
  value = aws_ssm_document.deploy.name
}
