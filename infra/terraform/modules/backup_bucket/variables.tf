variable "name" {
  description = "Bucket name (secondmind-backups-<account id>)."
  type        = string
}

variable "expire_days" {
  description = "Days a dump is kept."
  type        = number
  default     = 30
}

variable "tags" {
  type = map(string)
}
