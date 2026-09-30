variable "name" {
  description = "Prefix for resource names."
  type        = string
  default     = "secondmind"
}

variable "cidr_block" {
  description = "The VPC's address range."
  type        = string
  default     = "10.42.0.0/24"
}

variable "availability_zone" {
  description = "The one availability zone the host lives in."
  type        = string
}

variable "tags" {
  description = "Tags for every resource (project and env)."
  type        = map(string)
}
