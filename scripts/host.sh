#!/usr/bin/env bash
# Stop, start or look at the production host (guide Part 3). Yours to run, with your AWS profile.
#   scripts/host.sh stop|start|status
# Before launch nobody visits, so stopping the instance saves credit. What still draws while it is
# stopped: the Elastic IP (about $3.60 a month) and the disks and snapshots (about $2). After a
# start the site is back by itself: the address stays, Docker starts, every container restarts.
set -euo pipefail
action="${1:?usage: host.sh stop|start|status}"
region="${AWS_REGION:-us-east-1}"

id="$(aws ec2 describe-instances --region "$region" \
  --filters Name=tag:project,Values=secondmind \
            Name=instance-state-name,Values=pending,running,stopping,stopped \
  --query 'Reservations[].Instances[].InstanceId' --output text)"
[[ -n "$id" && "$id" != None && "$id" != *$'\t'* ]] || { echo "expected exactly one 2nd Mind instance, found: '${id:-none}'" >&2; exit 1; }

state() { aws ec2 describe-instances --region "$region" --instance-ids "$id" \
  --query 'Reservations[0].Instances[0].State.Name' --output text; }

case "$action" in
  status) echo "$id is $(state)" ;;
  stop)
    aws ec2 stop-instances --region "$region" --instance-ids "$id" >/dev/null
    aws ec2 wait instance-stopped --region "$region" --instance-ids "$id"
    echo "$id is stopped. The Elastic IP and the disks still draw credit (about \$5.60 a month together)." ;;
  start)
    aws ec2 start-instances --region "$region" --instance-ids "$id" >/dev/null
    aws ec2 wait instance-running --region "$region" --instance-ids "$id"
    echo "$id is running. The stack restarts by itself: give it a minute, then \`make smoke-prod\`." ;;
  *) echo "usage: host.sh stop|start|status" >&2; exit 2 ;;
esac
