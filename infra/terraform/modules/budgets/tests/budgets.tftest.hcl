mock_provider "aws" {}

variables {
  alert_emails = ["owner@example.test"]
  tags         = { project = "secondmind", env = "prod" }
}

run "the_net_budget_is_one_dollar_after_credits_and_alerts_on_the_first_cent" {
  command = apply

  assert {
    condition     = aws_budgets_budget.net.limit_amount == "1" && aws_budgets_budget.net.limit_unit == "USD" && aws_budgets_budget.net.time_unit == "MONTHLY"
    error_message = "net cost is capped at $1 a month"
  }

  assert {
    condition     = one(aws_budgets_budget.net.cost_types).include_credit == true
    error_message = "the net budget counts credits, so it is the cost after them"
  }

  assert {
    condition = anytrue([
      for n in aws_budgets_budget.net.notification :
      n.notification_type == "ACTUAL" && n.threshold_type == "ABSOLUTE_VALUE" && n.threshold == 0.01
    ])
    error_message = "an alert on the first cent of actual charge"
  }

  assert {
    condition = anytrue([
      for n in aws_budgets_budget.net.notification :
      n.notification_type == "FORECASTED" && n.threshold_type == "PERCENTAGE" && n.threshold == 100
    ])
    error_message = "a forecast alert at 100%"
  }
}

run "the_gross_budget_is_25_dollars_before_credits_and_alerts_at_80_percent" {
  command = apply

  assert {
    condition     = aws_budgets_budget.gross.limit_amount == "25" && aws_budgets_budget.gross.time_unit == "MONTHLY"
    error_message = "gross cost is capped at $25 a month"
  }

  assert {
    condition = (
      one(aws_budgets_budget.gross.cost_types).include_credit == false &&
      one(aws_budgets_budget.gross.cost_types).include_discount == false &&
      one(aws_budgets_budget.gross.cost_types).include_refund == false
    )
    error_message = "the gross budget leaves credits, discounts and refunds out: cost before credits"
  }

  assert {
    condition = anytrue([
      for n in aws_budgets_budget.gross.notification :
      n.notification_type == "ACTUAL" && n.threshold_type == "PERCENTAGE" && n.threshold == 80
    ])
    error_message = "an alert at 80% of actual"
  }

  assert {
    condition = anytrue([
      for n in aws_budgets_budget.gross.notification :
      n.notification_type == "FORECASTED" && n.threshold == 100
    ])
    error_message = "a forecast alert at 100%"
  }
}

run "a_budget_needs_somebody_to_tell" {
  command = plan
  variables {
    alert_emails = []
  }
  expect_failures = [var.alert_emails]
}
