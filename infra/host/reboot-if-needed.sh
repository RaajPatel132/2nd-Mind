#!/usr/bin/env bash
# The weekly window (see systemd/secondmind-reboot.timer): reboot only if a security update
# asked for it. The stack comes back by itself: Docker starts at boot and every container is
# `restart: unless-stopped`; the data volume is mounted before Docker starts.
set -euo pipefail
if [[ -f /var/run/reboot-required ]]; then
  echo "reboot required by: $(cat /var/run/reboot-required.pkgs 2>/dev/null | tr '\n' ' ')"
  systemctl reboot
else
  echo "no reboot needed"
fi
