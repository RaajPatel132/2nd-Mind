variable "aws_region" {
  description = "Where everything lives: model providers are in the US, so compute is next to them (ADR-0012)."
  type        = string
  default     = "us-east-1"
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
