#!/usr/bin/env bash
# Run something on the production host over SSM Run Command, with YOUR AWS credentials (the
# session never holds any). Output comes back here.
#
#   scripts/prod-ssm.sh admin <args>     the admin CLI in the api container, e.g. `admin kill-switch on`
#   scripts/prod-ssm.sh deploy <sha> [migrate|no-migrate]     deploy a release without GitHub
#   scripts/prod-ssm.sh shell '<command>'   any command, as root
#
# `deploy` is the way to roll back before the deploy workflow exists on main (GitHub only lists a
# workflow that is on the default branch), and a way in if GitHub is down.
set -euo pipefail
region="${AWS_REGION:-us-east-1}"
kind="${1:?usage: prod-ssm.sh admin|deploy|shell ...}"
shift

id="$(aws ec2 describe-instances --region "$region" \
  --filters Name=tag:project,Values=secondmind Name=instance-state-name,Values=running \
  --query 'Reservations[].Instances[].InstanceId' --output text)"
[[ -n "$id" && "$id" != None && "$id" != *$'\t'* ]] || { echo "expected exactly one running 2nd Mind instance, found: '${id:-none}'" >&2; exit 1; }

compose='cd /opt/secondmind && IMAGE_TAG="$(cat /var/lib/secondmind/current)" docker compose -p secondmind -f compose.prodlike.yaml --env-file /etc/secondmind/prod.env'
case "$kind" in
  admin)
    args=""; for a in "$@"; do args="$args $(printf '%q' "$a")"; done
    command="$compose exec -T api python -m secondmind.api.admin$args"
    document=AWS-RunShellScript
    parameters="$(python3 -c 'import json,sys; print(json.dumps({"commands": [sys.argv[1]]}))' "$command")" ;;
  shell)
    document=AWS-RunShellScript
    parameters="$(python3 -c 'import json,sys; print(json.dumps({"commands": [sys.argv[1]]}))' "$1")" ;;
  deploy)
    sha="$(git rev-parse "${1:?usage: prod-ssm.sh deploy <sha> [migrate|no-migrate]}")"
    migrate=true; [[ "${2:-migrate}" == no-migrate ]] && migrate=false
    document=secondmind-deploy
    parameters="$(python3 -c 'import json,sys; print(json.dumps({"sha": [sys.argv[1]], "migrate": [sys.argv[2]]}))' "$sha" "$migrate")" ;;
  *) echo "usage: prod-ssm.sh admin|deploy|shell ..." >&2; exit 2 ;;
esac

command_id="$(aws ssm send-command --region "$region" --instance-ids "$id" \
  --document-name "$document" --parameters "$parameters" \
  --comment "prod-ssm $kind by $(whoami)" --query Command.CommandId --output text)"
aws ssm wait command-executed --region "$region" --command-id "$command_id" --instance-id "$id" 2>/dev/null || true
status="$(aws ssm get-command-invocation --region "$region" --command-id "$command_id" --instance-id "$id" --query Status --output text)"
aws ssm get-command-invocation --region "$region" --command-id "$command_id" --instance-id "$id" \
  --query 'StandardOutputContent' --output text
err="$(aws ssm get-command-invocation --region "$region" --command-id "$command_id" --instance-id "$id" --query 'StandardErrorContent' --output text)"
[[ -z "$err" || "$err" == None ]] || echo "$err" >&2
[[ "$status" == Success ]] || { echo "SSM command $command_id: $status" >&2; exit 1; }
