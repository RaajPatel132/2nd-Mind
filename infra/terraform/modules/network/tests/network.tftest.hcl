# Offline: the AWS provider is mocked, so this needs no account. Run with `make tf-check`.
mock_provider "aws" {}

variables {
  availability_zone = "us-east-1a"
  tags              = { project = "secondmind", env = "prod" }
}

run "only_80_and_443_are_open_and_there_is_no_ssh" {
  command = apply

  assert {
    condition     = length(aws_vpc_security_group_ingress_rule.web) == 2
    error_message = "exactly two ingress rules: 80 and 443"
  }

  assert {
    condition = alltrue([
      for rule in values(aws_vpc_security_group_ingress_rule.web) :
      contains([80, 443], rule.from_port) && rule.from_port == rule.to_port && rule.ip_protocol == "tcp"
    ])
    error_message = "only tcp 80 and 443 may be open"
  }

  assert {
    condition     = !contains([for rule in values(aws_vpc_security_group_ingress_rule.web) : rule.from_port], 22)
    error_message = "SSH must not be open: access is by Session Manager"
  }
}

run "the_subnet_hands_out_no_public_address_of_its_own" {
  command = apply

  assert {
    condition     = aws_subnet.public.map_public_ip_on_launch == false
    error_message = "the Elastic IP is the only public address"
  }
}

run "resources_carry_the_project_and_env_tags" {
  command = apply

  assert {
    condition     = aws_vpc.this.tags["project"] == "secondmind" && aws_vpc.this.tags["env"] == "prod"
    error_message = "the VPC must be tagged project and env"
  }
}
