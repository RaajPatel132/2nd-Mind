# The platform root (ADR-0035): what a host needs, whichever project runs on it. The host, its
# network, its role, its snapshots and the two Budgets. Later projects on <domain> share these;
# this root moves to its own repository when a second project arrives.
#
# DNS is not here: it is one A record at Cloudflare, added by hand from the `dns_record` output
# (guide step 8), so it survives the move to another host untouched.

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = local.tags
  }
}

data "aws_caller_identity" "current" {}

data "aws_availability_zones" "available" {
  state = "available"
}

locals {
  tags = {
    project = "secondmind"
    env     = "prod"
  }
  availability_zone = coalesce(var.availability_zone, data.aws_availability_zones.available.names[0])
  app_hostname      = "${var.app_subdomain}.${var.domain_name}"
  # Made by the app root under the same name, so the host's role can be scoped to it.
  backup_bucket_name = "secondmind-backups-${data.aws_caller_identity.current.account_id}"
}

module "network" {
  source            = "../modules/network"
  availability_zone = local.availability_zone
  tags              = local.tags
}

module "host" {
  source             = "../modules/host"
  availability_zone  = local.availability_zone
  subnet_id          = module.network.subnet_id
  security_group_id  = module.network.security_group_id
  backup_bucket_name = local.backup_bucket_name
  repo_url           = "https://github.com/${var.github_repo}.git"
  repo_ref           = var.repo_ref
  tags               = local.tags
}

module "budgets" {
  source       = "../modules/budgets"
  alert_emails = [var.alert_email]
  tags         = local.tags
}
