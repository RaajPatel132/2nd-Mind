#!/usr/bin/env bash
# Host setup for 2nd Mind on a fresh Ubuntu 24.04 machine (ADR-0035). Plain Linux: nothing here
# knows about AWS, so the same script sets up an Oracle instance or a VPS. The AWS-only half is
# aws/setup-aws.sh, which the EC2 first-boot script runs after this one.
#
# Idempotent: running it again changes nothing that is already right.
#
# Environment (all optional):
#   DATA_DEVICE  block device for the data volume (Postgres, Redis, the edge's certificates). It
#                is formatted only when it has no filesystem. Unset: the data lives on the root
#                disk under DATA_DIR.
#   REPO_URL     the public repository to deploy from (default: the project's)
#   REPO_REF     branch or tag to check out first (deploys check out a SHA afterwards)
#   SWAP_SIZE    swap file size (default 2G): a cushion for a 2 GB host, not working memory
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/secondmind}"
DATA_DIR="${DATA_DIR:-/srv/secondmind}"
STATE_DIR=/var/lib/secondmind
CONF_DIR=/etc/secondmind
SWAP_SIZE="${SWAP_SIZE:-2G}"
REPO_URL="${REPO_URL:-https://github.com/RaajPatel132/2nd-Mind.git}"
REPO_REF="${REPO_REF:-main}"
export DEBIAN_FRONTEND=noninteractive

log() { echo "==> $*"; }

[[ $EUID -eq 0 ]] || { echo "run as root" >&2; exit 1; }

# ------------------------------------------------------------------ packages
log "packages"
apt-get update -qq
apt-get install -y -qq ca-certificates curl gnupg git jq unzip unattended-upgrades

if ! command -v docker >/dev/null 2>&1; then
  log "docker"
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update -qq
  apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-compose-plugin
fi

# ------------------------------------------------------------------ swap
if ! swapon --show=NAME --noheadings | grep -q '^/swapfile$'; then
  log "swap file ($SWAP_SIZE)"
  fallocate -l "$SWAP_SIZE" /swapfile
  chmod 600 /swapfile
  mkswap /swapfile >/dev/null
  swapon /swapfile
  grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi
echo 'vm.swappiness=10' > /etc/sysctl.d/99-secondmind.conf
sysctl -q -p /etc/sysctl.d/99-secondmind.conf

# ------------------------------------------------------------------ the data volume
mkdir -p "$DATA_DIR"
if [[ -n "${DATA_DEVICE:-}" ]]; then
  log "data volume $DATA_DEVICE -> $DATA_DIR"
  for _ in $(seq 1 60); do [[ -b "$DATA_DEVICE" ]] && break; sleep 5; done
  [[ -b "$DATA_DEVICE" ]] || { echo "data device $DATA_DEVICE did not appear" >&2; exit 1; }
  if ! blkid "$DATA_DEVICE" >/dev/null 2>&1; then
    mkfs.ext4 -q -L secondmind-data "$DATA_DEVICE"
  fi
  grep -q 'LABEL=secondmind-data' /etc/fstab \
    || echo "LABEL=secondmind-data $DATA_DIR ext4 defaults,nofail 0 2" >> /etc/fstab
  mountpoint -q "$DATA_DIR" || mount "$DATA_DIR"
  # Docker must not start a database on the root disk because the volume was late.
  mkdir -p /etc/systemd/system/docker.service.d
  printf '[Unit]\nRequiresMountsFor=%s\n' "$DATA_DIR" \
    > /etc/systemd/system/docker.service.d/10-secondmind-data.conf
  systemctl daemon-reload
fi
mkdir -p "$DATA_DIR/postgres" "$DATA_DIR/redis" "$DATA_DIR/caddy-data" "$DATA_DIR/caddy-config"

systemctl enable --now docker

# Named volumes that live on the data volume. The compose project is `secondmind`, so compose
# finds these by name and uses them (the labels make it treat them as its own).
for volume in postgres-data:postgres redis-data:redis caddy-data:caddy-data caddy-config:caddy-config; do
  name="secondmind_${volume%%:*}"
  dir="$DATA_DIR/${volume##*:}"
  docker volume inspect "$name" >/dev/null 2>&1 || docker volume create --driver local \
    --opt type=none --opt o=bind --opt "device=$dir" \
    --label com.docker.compose.project=secondmind \
    --label "com.docker.compose.volume=${volume%%:*}" "$name" >/dev/null
done

# ------------------------------------------------------------------ the code and state
mkdir -p "$CONF_DIR" "$STATE_DIR" /var/backups/secondmind
chmod 700 "$CONF_DIR"
if [[ ! -d "$APP_DIR/.git" ]]; then
  log "checkout $REPO_URL ($REPO_REF)"
  git clone --depth 1 --branch "$REPO_REF" "$REPO_URL" "$APP_DIR"
fi

# ------------------------------------------------------------------ patching
log "unattended upgrades (security updates; reboots in the weekly window)"
cat > /etc/apt/apt.conf.d/52secondmind-unattended <<'CONF'
Unattended-Upgrade::Automatic-Reboot "false";
Unattended-Upgrade::Remove-Unused-Dependencies "true";
CONF
cat > /etc/apt/apt.conf.d/20auto-upgrades <<'CONF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
CONF

# ------------------------------------------------------------------ timers: nightly dump, weekly reboot
log "systemd timers"
for unit in secondmind-backup.service secondmind-backup.timer \
            secondmind-reboot.service secondmind-reboot.timer; do
  install -m 0644 "$APP_DIR/infra/host/systemd/$unit" "/etc/systemd/system/$unit"
done
systemctl daemon-reload
systemctl enable --now secondmind-backup.timer secondmind-reboot.timer

log "host setup done; the first deploy brings the stack up"
