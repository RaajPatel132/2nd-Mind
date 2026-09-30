mock_provider "aws" {
  mock_data "aws_caller_identity" {
    defaults = { account_id = "123456789012" }
  }
  mock_data "aws_region" {
    defaults = { region = "us-east-1", name = "us-east-1" }
  }
}

variables {
  github_repo          = "owner/repo"
  deploy_document_name = "secondmind-deploy"
  state_bucket         = "secondmind-tfstate-123456789012"
  tags                 = { project = "secondmind", env = "prod" }
}

run "the_deploy_role_trusts_only_this_repos_production_environment" {
  command = apply

  assert {
    condition     = jsondecode(aws_iam_role.deploy.assume_role_policy).Statement[0].Condition.StringEquals["token.actions.githubusercontent.com:sub"] == "repo:owner/repo:environment:production"
    error_message = "the deploy role trusts exactly repo:owner/repo:environment:production"
  }

  assert {
    condition     = !can(jsondecode(aws_iam_role.deploy.assume_role_policy).Statement[0].Condition.StringLike)
    error_message = "no wildcard match on the deploy role's subject"
  }

  assert {
    condition     = jsondecode(aws_iam_role.deploy.assume_role_policy).Statement[0].Condition.StringEquals["token.actions.githubusercontent.com:aud"] == "sts.amazonaws.com"
    error_message = "the audience must be sts.amazonaws.com"
  }

  assert {
    condition     = jsondecode(aws_iam_role.deploy.assume_role_policy).Statement[0].Action == "sts:AssumeRoleWithWebIdentity"
    error_message = "only web identity federation may assume it: no user or account principal"
  }
}

run "the_deploy_role_can_only_send_our_document_to_our_instance" {
  command = apply

  assert {
    condition = toset(flatten([
      for s in jsondecode(aws_iam_role_policy.deploy.policy).Statement : tolist(s.Action)
    ])) == toset(["ssm:SendCommand", "ssm:GetCommandInvocation", "ssm:ListCommandInvocations", "ssm:ListCommands"])
    error_message = "the deploy role's actions changed: it may only send our document and watch it"
  }

  assert {
    condition = alltrue([
      for s in jsondecode(aws_iam_role_policy.deploy.policy).Statement :
      contains(tolist(s.Action), "ssm:SendCommand") ? !contains(tolist(s.Resource), "*") : true
    ])
    error_message = "SendCommand must never be allowed on Resource *"
  }

  assert {
    condition = anytrue([
      for s in jsondecode(aws_iam_role_policy.deploy.policy).Statement :
      contains(tolist(s.Resource), "arn:aws:ssm:us-east-1:123456789012:document/secondmind-deploy")
    ])
    error_message = "the document is named, not wildcarded"
  }

  assert {
    condition = anytrue([
      for s in jsondecode(aws_iam_role_policy.deploy.policy).Statement :
      try(s.Condition.StringEquals["ssm:resourceTag/project"], "") == "secondmind"
    ])
    error_message = "instances are limited to those tagged project=secondmind"
  }
}

run "the_plan_role_is_read_only_and_works_from_any_branch_of_this_repo" {
  command = apply

  assert {
    condition     = aws_iam_role_policy_attachment.plan_read_only.policy_arn == "arn:aws:iam::aws:policy/ReadOnlyAccess"
    error_message = "the plan role has read-only access"
  }

  assert {
    condition     = toset(jsondecode(aws_iam_role.plan.assume_role_policy).Statement[0].Condition.StringLike["token.actions.githubusercontent.com:sub"]) == toset(["repo:owner/repo:ref:refs/heads/*", "repo:owner/repo:pull_request"])
    error_message = "any branch of this repo and its pull requests, and nothing else"
  }

  assert {
    condition = alltrue([
      for s in jsondecode(aws_iam_role_policy.plan_state.policy).Statement :
      alltrue([for a in tolist(s.Action) : startswith(a, "s3:Get") || startswith(a, "s3:List")])
    ])
    error_message = "the plan role only reads the state bucket"
  }
}

run "a_repo_with_a_wildcard_is_refused" {
  command = plan
  variables {
    github_repo = "owner/*"
  }
  expect_failures = [var.github_repo]
}

run "no_access_key_exists" {
  command = apply

  assert {
    condition     = length(aws_iam_openid_connect_provider.github) == 1
    error_message = "CI trades a GitHub token for temporary credentials through OIDC; there is no aws_iam_access_key in this module"
  }
}
