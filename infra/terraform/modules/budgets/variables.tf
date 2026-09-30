variable "name" {
  type    = string
  default = "secondmind"
}

variable "alert_emails" {
  description = "Who the Budgets email."
  type        = list(string)

  validation {
    condition     = length(var.alert_emails) > 0
    error_message = "A Budget with nobody to tell is no alarm: give at least one address."
  }
}

variable "tags" {
  type = map(string)
}
