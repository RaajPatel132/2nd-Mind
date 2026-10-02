variable "aws_region" {
  description = "Where everything lives. This account's organisation only allows ap-southeast-2 (an SCP denies EC2 in us-east-1), so ADR-0012's us-east-1 is not available."
  type        = string
  default     = "ap-southeast-2"
}

variable "domain_name" {
  description = "The domain you bought, e.g. example.com. Nothing in the repository has one written into it."
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$", var.domain_name))
    error_message = "domain_name is a plain domain such as example.com."
  }
}

variable "app_subdomain" {
  description = "The app's host name is <app_subdomain>.<domain_name>."
  type        = string
  default     = "2nd-mind"
}

variable "alert_email" {
  description = "Who the two Budgets email."
  type        = string
}

variable "github_repo" {
  description = "owner/name of the public GitHub repository the host fetches its setup and deploys from."
  type        = string
}

variable "repo_ref" {
  description = "Branch or tag the host checks out at first boot. Deploys check out a SHA afterwards."
  type        = string
  default     = "main"
}

variable "availability_zone" {
  description = "The one zone the host lives in; the first available one when unset."
  type        = string
  default     = null
}
