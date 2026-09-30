variable "name" {
  description = "Prefix for resource names."
  type        = string
  default     = "secondmind"
}

variable "availability_zone" {
  description = "The availability zone of the host and its data volume."
  type        = string
}

variable "subnet_id" {
  type = string
}

variable "security_group_id" {
  type = string
}

variable "instance_type" {
  description = "t4g.small (2 vCPU, 2 GB) is what the Free plan allows and the app is measured to fit."
  type        = string
  default     = "t4g.small"

  validation {
    condition     = var.instance_type == "t4g.small"
    error_message = "The host is a t4g.small. A bigger size may not be allowed on the Free plan and draws more credit: ask first (ADR-0035)."
  }
}

variable "root_volume_gb" {
  description = "Root volume size. Every GB draws credit, so as small as is safe."
  type        = number
  default     = 12
}

variable "data_volume_gb" {
  description = "Data volume (Postgres, Redis, the edge's certificates). Every GB draws credit."
  type        = number
  default     = 10
}

variable "ssm_path" {
  description = "Where the app's parameters live; the host may read only under it."
  type        = string
  default     = "/secondmind/prod"
}

variable "backup_bucket_name" {
  description = "The backup bucket the host may write dumps to (made by the app root)."
  type        = string
}

variable "log_group_name" {
  description = "CloudWatch log group for the containers' logs (kept 7 days)."
  type        = string
  default     = "/secondmind/prod"
}

variable "repo_url" {
  description = "The public repository the host fetches its setup and deploys from."
  type        = string
}

variable "repo_ref" {
  description = "Branch or tag checked out at first boot (a deploy checks out a SHA afterwards)."
  type        = string
  default     = "main"
}

variable "tags" {
  description = "Tags for every resource (project and env)."
  type        = map(string)
}
