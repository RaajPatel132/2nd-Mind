#!/usr/bin/env bash
# Point a DuckDNS name at the host's address: the one DNS record the site needs (ADR-0035).
#   DUCKDNS_TOKEN_FILE=<file> scripts/dns-duckdns.sh second-mind.duckdns.org 203.0.113.7
# The token comes from DUCKDNS_TOKEN or from the file named by DUCKDNS_TOKEN_FILE, never from an
# argument (it would show in `ps`), and nothing in the repository holds it. Plain curl, nothing
# AWS: after a move to another host, run it again with the new address.
set -euo pipefail
usage="usage: dns-duckdns.sh <name>.duckdns.org <ipv4>"
host="${1:?$usage}"
ip="${2:?$usage}"
[[ "$host" =~ ^[a-z0-9][a-z0-9-]*\.duckdns\.org$ ]] || { echo "not a duckdns.org name: $host" >&2; exit 2; }
[[ "$ip" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]] || { echo "not an IPv4 address: $ip" >&2; exit 2; }

token="${DUCKDNS_TOKEN:-}"
if [[ -z "$token" && -n "${DUCKDNS_TOKEN_FILE:-}" ]]; then token="$(<"$DUCKDNS_TOKEN_FILE")"; fi
[[ -n "$token" ]] || { echo "set DUCKDNS_TOKEN or DUCKDNS_TOKEN_FILE" >&2; exit 2; }

# curl reads its request from stdin, so the token is never on a command line.
answer="$(printf 'url = "https://www.duckdns.org/update?domains=%s&token=%s&ip=%s"\n' \
  "${host%.duckdns.org}" "$token" "$ip" | curl -fsS --max-time 20 -K -)"
[[ "$answer" == OK* ]] || { echo "DuckDNS answered: ${answer:-nothing}" >&2; exit 1; }
echo "$host -> $ip (DuckDNS said OK)"

# The name must resolve before the stack starts: Let's Encrypt allows only a few failed
# validations an hour, and Caddy asks for the certificate the first time it is started.
if command -v dig >/dev/null 2>&1; then
  for _ in $(seq 1 18); do
    [[ "$(dig +short "$host" @1.1.1.1 | tail -n 1)" == "$ip" ]] && { echo "resolves to $ip"; exit 0; }
    sleep 5
  done
  echo "DuckDNS accepted it but $host does not resolve to $ip yet; check again with: dig +short $host" >&2
  exit 1
fi
