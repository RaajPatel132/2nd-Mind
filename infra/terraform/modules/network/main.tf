# A small dedicated VPC: one public subnet in one AZ, an internet gateway, no NAT gateway (the
# credits-only rule: a NAT gateway is about $32 a month for nothing but outbound calls), and a
# security group that lets only web traffic in. There is no SSH: access is by Session Manager.

terraform {
  required_version = ">= 1.10"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.66"
    }
  }
}

resource "aws_vpc" "this" {
  cidr_block           = var.cidr_block
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags                 = merge(var.tags, { Name = "${var.name}-vpc" })
}

resource "aws_internet_gateway" "this" {
  vpc_id = aws_vpc.this.id
  tags   = merge(var.tags, { Name = "${var.name}-igw" })
}

resource "aws_subnet" "public" {
  vpc_id            = aws_vpc.this.id
  cidr_block        = cidrsubnet(var.cidr_block, 4, 0)
  availability_zone = var.availability_zone
  # The Elastic IP is the only public address (an auto-assigned one would be a second, billed).
  map_public_ip_on_launch = false
  tags                    = merge(var.tags, { Name = "${var.name}-public" })
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.this.id
  tags   = merge(var.tags, { Name = "${var.name}-public" })
}

resource "aws_route" "internet" {
  route_table_id         = aws_route_table.public.id
  destination_cidr_block = "0.0.0.0/0"
  gateway_id             = aws_internet_gateway.this.id
}

resource "aws_route_table_association" "public" {
  subnet_id      = aws_subnet.public.id
  route_table_id = aws_route_table.public.id
}

resource "aws_security_group" "web" {
  name        = "${var.name}-web"
  description = "Web traffic in (80 and 443), everything out. No SSH: Session Manager."
  vpc_id      = aws_vpc.this.id
  tags        = merge(var.tags, { Name = "${var.name}-web" })
}

# 80 answers with a redirect to HTTPS, and is where Let's Encrypt validates the host name.
resource "aws_vpc_security_group_ingress_rule" "web" {
  for_each          = toset(["80", "443"])
  security_group_id = aws_security_group.web.id
  description       = "HTTP(S) from anywhere"
  ip_protocol       = "tcp"
  from_port         = tonumber(each.value)
  to_port           = tonumber(each.value)
  cidr_ipv4         = "0.0.0.0/0"
  tags              = var.tags
}

# The host pulls images and packages and calls the model providers.
resource "aws_vpc_security_group_egress_rule" "all" {
  security_group_id = aws_security_group.web.id
  description       = "Everything out: image pulls, updates, model providers, Let's Encrypt"
  ip_protocol       = "-1"
  cidr_ipv4         = "0.0.0.0/0"
  tags              = var.tags
}
