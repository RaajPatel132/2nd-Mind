#!/usr/bin/env bash
# The AWS half of host setup, run once at first boot after ../setup.sh: the AWS CLI, container
# logs to CloudWatch, and the host's settings file. Environment: AWS_REGION, SSM_PATH,
# BACKUP_BUCKET, LOG_GROUP.
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo "run as root" >&2; exit 1; }
: "${AWS_REGION:?}" "${SSM_PATH:?}" "${BACKUP_BUCKET:?}" "${LOG_GROUP:?}"

if ! command -v aws >/dev/null 2>&1; then
  echo "==> aws cli"
  arch="$(uname -m)"   # aarch64 on Graviton
  curl -fsSL "https://awscli.amazonaws.com/awscli-exe-linux-${arch}.zip" -o /tmp/awscliv2.zip
  unzip -q -o /tmp/awscliv2.zip -d /tmp
  /tmp/aws/install --update
  rm -rf /tmp/aws /tmp/awscliv2.zip
fi

# The session manager agent comes with the Ubuntu AMI as a snap; make sure it runs.
snap list amazon-ssm-agent >/dev/null 2>&1 && systemctl enable --now snap.amazon-ssm-agent.amazon-ssm-agent.service || true

echo "==> container logs to CloudWatch ($LOG_GROUP)"
mkdir -p /etc/docker
cat > /etc/docker/daemon.json <<JSON
{
  "log-driver": "awslogs",
  "log-opts": {
    "awslogs-region": "$AWS_REGION",
    "awslogs-group": "$LOG_GROUP",
    "awslogs-create-group": "false",
    "tag": "{{.Name}}"
  }
}
JSON
systemctl restart docker

echo "==> host settings"
cat > /etc/secondmind/host.conf <<CONF
ENV_SOURCE=ssm
AWS_REGION=$AWS_REGION
SSM_PATH=$SSM_PATH
BACKUP_UPLOAD=aws
BACKUP_BUCKET=$BACKUP_BUCKET
CONF
chmod 600 /etc/secondmind/host.conf
