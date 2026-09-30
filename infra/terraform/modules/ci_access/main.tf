# CI's access to AWS: GitHub's OIDC provider and two roles. No AWS access key exists anywhere;
# a workflow trades its short-lived GitHub token for a role's temporary credentials.
#
#   plan    read-only, from any branch of this repository (and its pull requests)
#   deploy  sends our one deploy document to our instance, and nothing else; works only from the
#           `production` GitHub environment, whose branch rule allows main and, until launch,
#           sprint-* branches

terraform {
  required_version = ">= 1.10"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.66"
    }
  }
}

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

locals {
  account_id   = data.aws_caller_identity.current.account_id
  region       = data.aws_region.current.region
  oidc_host    = "token.actions.githubusercontent.com"
  oidc_arn     = var.create_oidc_provider ? aws_iam_openid_connect_provider.github[0].arn : "arn:aws:iam::${local.account_id}:oidc-provider/${local.oidc_host}"
  state_bucket = "arn:aws:s3:::${var.state_bucket}"

  deploy_subject = "repo:${var.github_repo}:environment:${var.environment_name}"
  plan_subjects = [
    "repo:${var.github_repo}:ref:refs/heads/*",
    "repo:${var.github_repo}:pull_request",
  ]
}

resource "aws_iam_openid_connect_provider" "github" {
  count          = var.create_oidc_provider ? 1 : 0
  url            = "https://${local.oidc_host}"
  client_id_list = ["sts.amazonaws.com"]
  tags           = var.tags
}

# ---------------------------------------------------------------- plan: read-only
resource "aws_iam_role" "plan" {
  name                 = "${var.name}-ci-plan"
  max_session_duration = 3600
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Federated = local.oidc_arn }
      Action    = "sts:AssumeRoleWithWebIdentity"
      Condition = {
        StringEquals = { "${local.oidc_host}:aud" = "sts.amazonaws.com" }
        StringLike   = { "${local.oidc_host}:sub" = local.plan_subjects }
      }
    }]
  })
  tags = var.tags
}

resource "aws_iam_role_policy_attachment" "plan_read_only" {
  role       = aws_iam_role.plan.name
  policy_arn = "arn:aws:iam::aws:policy/ReadOnlyAccess"
}

# A plan reads the state (and takes no lock: `plan -lock=false`), and changes nothing.
resource "aws_iam_role_policy" "plan_state" {
  name = "read-terraform-state"
  role = aws_iam_role.plan.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["s3:GetObject"]
        Resource = ["${local.state_bucket}/*"]
      },
      {
        Effect   = "Allow"
        Action   = ["s3:ListBucket"]
        Resource = [local.state_bucket]
      },
    ]
  })
}

# ---------------------------------------------------------------- deploy: one document, one instance
resource "aws_iam_role" "deploy" {
  name                 = "${var.name}-ci-deploy"
  max_session_duration = 3600
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Federated = local.oidc_arn }
      Action    = "sts:AssumeRoleWithWebIdentity"
      Condition = {
        StringEquals = {
          "${local.oidc_host}:aud" = "sts.amazonaws.com"
          "${local.oidc_host}:sub" = local.deploy_subject
        }
      }
    }]
  })
  tags = var.tags
}

resource "aws_iam_role_policy" "deploy" {
  name = "send-the-deploy-document"
  role = aws_iam_role.deploy.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "RunOurDeployDocument"
        Effect   = "Allow"
        Action   = ["ssm:SendCommand"]
        Resource = ["arn:aws:ssm:${local.region}:${local.account_id}:document/${var.deploy_document_name}"]
      },
      {
        Sid      = "OnOurInstanceOnly"
        Effect   = "Allow"
        Action   = ["ssm:SendCommand"]
        Resource = ["arn:aws:ec2:${local.region}:${local.account_id}:instance/*"]
        Condition = {
          StringEquals = { "ssm:resourceTag/project" = "secondmind" }
        }
      },
      {
        # These read a command's progress and don't support resource-level permissions.
        Sid      = "WatchTheCommand"
        Effect   = "Allow"
        Action   = ["ssm:GetCommandInvocation", "ssm:ListCommandInvocations", "ssm:ListCommands"]
        Resource = ["*"]
      },
    ]
  })
}
