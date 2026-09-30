variable "name" {
  type    = string
  default = "secondmind"
}

variable "github_repo" {
  description = "owner/name of the GitHub repository whose workflows may assume the roles."
  type        = string

  validation {
    condition     = can(regex("^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", var.github_repo))
    error_message = "github_repo is owner/name, with no wildcard."
  }
}

variable "environment_name" {
  description = "The GitHub environment the deploy role trusts."
  type        = string
  default     = "production"
}

variable "deploy_document_name" {
  description = "The SSM document the deploy role may send."
  type        = string
}

variable "state_bucket" {
  description = "The Terraform state bucket the plan role may read."
  type        = string
}

variable "create_oidc_provider" {
  description = "GitHub's OIDC provider exists once per account: set false if it already does."
  type        = bool
  default     = true
}

variable "tags" {
  type = map(string)
}
