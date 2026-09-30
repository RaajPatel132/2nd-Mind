# The one host (ADR-0035): an arm64 Ubuntu 24.04 t4g.small with an Elastic IP, an encrypted root
# volume and a separate encrypted data volume that outlives the instance, a role that can do only
# what the host needs, daily snapshots of the data volume, and a 7-day log group. First boot
# fetches the repository and runs its plain-Linux setup script (infra/host/): nothing about the
# host's own setup is written here, so the same script sets up an Oracle instance or a VPS.

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

# Canonical's own pointer to the current Ubuntu 24.04 LTS arm64 image.
data "aws_ssm_parameter" "ubuntu" {
  name = "/aws/service/canonical/ubuntu/server/24.04/stable/current/arm64/hvm/ebs-gp3/ami-id"
}

# The account's default key for SSM SecureString parameters.
data "aws_kms_alias" "ssm" {
  name = "alias/aws/ssm"
}

locals {
  region         = data.aws_region.current.region
  account_id     = data.aws_caller_identity.current.account_id
  ssm_path       = trimsuffix(var.ssm_path, "/")
  parameters_arn = "arn:aws:ssm:${local.region}:${local.account_id}:parameter${local.ssm_path}"
  backup_bucket  = "arn:aws:s3:::${var.backup_bucket_name}"
  data_device    = "/dev/disk/by-id/nvme-Amazon_Elastic_Block_Store_${replace(aws_ebs_volume.data.id, "-", "")}"
  log_group_name = var.log_group_name
  instance_policy = {
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "ReadThisProjectsParameters"
        Effect = "Allow"
        Action = ["ssm:GetParameter", "ssm:GetParameters", "ssm:GetParametersByPath"]
        Resource = [
          local.parameters_arn,
          "${local.parameters_arn}/*",
        ]
      },
      {
        Sid      = "DecryptThoseParametersThroughSsm"
        Effect   = "Allow"
        Action   = ["kms:Decrypt"]
        Resource = [data.aws_kms_alias.ssm.target_key_arn]
        Condition = {
          StringEquals = { "kms:ViaService" = "ssm.${local.region}.amazonaws.com" }
        }
      },
      {
        Sid    = "WriteBackups"
        Effect = "Allow"
        Action = ["s3:PutObject", "s3:AbortMultipartUpload", "s3:ListMultipartUploadParts"]
        Resource = [
          "${local.backup_bucket}/dumps/*",
        ]
      },
      {
        Sid      = "ListTheBackupBucket"
        Effect   = "Allow"
        Action   = ["s3:ListBucket"]
        Resource = [local.backup_bucket]
      },
      {
        Sid      = "SendContainerLogs"
        Effect   = "Allow"
        Action   = ["logs:CreateLogStream", "logs:PutLogEvents", "logs:DescribeLogStreams"]
        Resource = ["${aws_cloudwatch_log_group.app.arn}:*"]
      },
    ]
  }
}

# ---------------------------------------------------------------- the host's role
resource "aws_iam_role" "host" {
  name = "${var.name}-host"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
  tags = var.tags
}

# The SSM agent: Session Manager and Run Command (deploys), and nothing else from this policy.
resource "aws_iam_role_policy_attachment" "ssm_agent" {
  role       = aws_iam_role.host.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_role_policy" "host" {
  name   = "${var.name}-host"
  role   = aws_iam_role.host.id
  policy = jsonencode(local.instance_policy)
}

resource "aws_iam_instance_profile" "host" {
  name = "${var.name}-host"
  role = aws_iam_role.host.name
  tags = var.tags
}

# ---------------------------------------------------------------- logs
resource "aws_cloudwatch_log_group" "app" {
  name              = local.log_group_name
  retention_in_days = 7
  tags              = var.tags
}

# ---------------------------------------------------------------- the data volume
resource "aws_ebs_volume" "data" {
  availability_zone = var.availability_zone
  size              = var.data_volume_gb
  type              = "gp3"
  encrypted         = true
  # Data Lifecycle Manager snapshots every volume tagged this way.
  tags = merge(var.tags, { Name = "${var.name}-data", Backup = "daily" })
}

# ---------------------------------------------------------------- the instance
resource "aws_instance" "host" {
  ami                    = data.aws_ssm_parameter.ubuntu.value
  instance_type          = var.instance_type
  subnet_id              = var.subnet_id
  vpc_security_group_ids = [var.security_group_id]
  iam_instance_profile   = aws_iam_instance_profile.host.name
  # No key pair: there is no SSH. Access is by Session Manager.

  # IMDSv2 only, one hop: a container (a second hop) can't reach the instance metadata service
  # and its role credentials.
  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
  }

  root_block_device {
    volume_size           = var.root_volume_gb
    volume_type           = "gp3"
    encrypted             = true
    delete_on_termination = true
    tags                  = merge(var.tags, { Name = "${var.name}-root" })
  }

  user_data = templatefile("${path.module}/../../../host/aws/user-data.sh.tftpl", {
    data_device   = local.data_device
    repo_url      = var.repo_url
    repo_ref      = var.repo_ref
    aws_region    = local.region
    ssm_path      = local.ssm_path
    backup_bucket = var.backup_bucket_name
    log_group     = local.log_group_name
  })
  # A new image or a changed first-boot script doesn't replace a running host: replacing it is
  # deliberate (`terraform apply -replace=...`), and the data volume comes along.
  lifecycle {
    ignore_changes = [ami, user_data]
  }

  tags = merge(var.tags, { Name = var.name })
}

resource "aws_volume_attachment" "data" {
  device_name                    = "/dev/sdf"
  volume_id                      = aws_ebs_volume.data.id
  instance_id                    = aws_instance.host.id
  stop_instance_before_detaching = true
}

# The address DNS points at. It stays when the instance is stopped, and costs about $3.60 a month
# either way (it draws credit even while the host is stopped).
resource "aws_eip" "host" {
  domain   = "vpc"
  instance = aws_instance.host.id
  tags     = merge(var.tags, { Name = var.name })
}

# ---------------------------------------------------------------- daily snapshots, 7 kept
resource "aws_iam_role" "dlm" {
  name = "${var.name}-snapshots"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "dlm.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
  tags = var.tags
}

resource "aws_iam_role_policy_attachment" "dlm" {
  role       = aws_iam_role.dlm.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSDataLifecycleManagerServiceRole"
}

resource "aws_dlm_lifecycle_policy" "data" {
  # The DLM API allows only letters, digits, spaces, dashes and underscores here.
  description        = "Daily snapshots of the ${var.name} data volume - 7 kept"
  execution_role_arn = aws_iam_role.dlm.arn
  state              = "ENABLED"
  tags               = var.tags

  policy_details {
    resource_types = ["VOLUME"]
    target_tags    = { Backup = "daily" }

    schedule {
      name      = "daily"
      copy_tags = true

      create_rule {
        interval      = 24
        interval_unit = "HOURS"
        times         = ["20:45"]
      }

      retain_rule {
        count = 7
      }

      tags_to_add = { SnapshotOf = var.name }
    }
  }
}
