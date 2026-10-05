variable "aws_region" {
  type    = string
  default = "ap-southeast-2"
}

variable "github_repo" {
  description = "owner/name of the GitHub repository whose workflows may assume the CI roles."
  type        = string
}

variable "github_environment" {
  description = "The GitHub environment the deploy role trusts (its branch rule allows main and, until launch, sprint-*)."
  type        = string
  default     = "production"
}

variable "enable_ci_access" {
  description = "Make GitHub's OIDC provider and the CI roles. Set false where the account's organisation policy denies the provider: deploys then run from a shell (make prod-deploy)."
  type        = bool
  default     = true
}

variable "create_oidc_provider" {
  description = "GitHub's OIDC provider exists once per account: set false if the account already has it."
  type        = bool
  default     = true
}
