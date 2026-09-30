# Two Budgets (decision 6): the alarm for the "no cash" promise, and the alarm for credits that
# drain faster than planned. The first two Budgets of an account cost nothing.

terraform {
  required_version = ">= 1.10"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.66"
    }
  }
}

# Net cost: what would actually be charged, after credits. Any charge at all emails you.
resource "aws_budgets_budget" "net" {
  name         = "${var.name}-net-monthly"
  budget_type  = "COST"
  limit_amount = "1"
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  cost_types {
    # Credits are negative line items: counting them nets them off the bill.
    include_credit       = true
    include_discount     = true
    include_refund       = true
    include_recurring    = true
    include_subscription = true
    include_support      = true
    include_tax          = true
    include_upfront      = true
    use_amortized        = false
    use_blended          = false
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 0.01
    threshold_type             = "ABSOLUTE_VALUE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = var.alert_emails
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "FORECASTED"
    subscriber_email_addresses = var.alert_emails
  }

  tags = var.tags
}

# Gross cost: before credits, so it shows the credit burn. The plan is about $18 a month.
resource "aws_budgets_budget" "gross" {
  name         = "${var.name}-gross-monthly"
  budget_type  = "COST"
  limit_amount = "25"
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  cost_types {
    include_credit       = false
    include_discount     = false
    include_refund       = false
    include_recurring    = true
    include_subscription = true
    include_support      = true
    include_tax          = true
    include_upfront      = true
    use_amortized        = false
    use_blended          = false
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 80
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = var.alert_emails
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "FORECASTED"
    subscriber_email_addresses = var.alert_emails
  }

  tags = var.tags
}
