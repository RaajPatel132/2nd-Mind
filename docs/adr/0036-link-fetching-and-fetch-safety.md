# ADR-0036: Link fetching and fetch safety

- **Status:** accepted (part 1, S4.6); part 2 (extraction, chunking, partial rules, video, cost) is
  written with S4.7
- **Date:** 2026-09-30

## Context

FR-2.8 and NFR-1.2: a person pastes a link and the app reads it. The public app must never be a way
into anything private (the instance's own metadata service, the database, other hosts on the
network), and never an open proxy for someone else's requests. The page is written by a stranger
and may be hostile; what to do with its *content* is ADR-0037.

## Decision

**One door.** `SafeFetcher` (`links/adapters/fetcher.py`) is the only way the app requests a URL a
person gave. An import-linter contract keeps the HTTP libraries out of every other module, and a
test scans the source for the standard library's own clients. Nothing fetches a URL found *inside*
fetched content.

**Rules, each with a test** (`tests/unit/links/`, and real servers in
`tests/integration/test_link_fetch_real_servers.py`):

- Only `http` and `https`, only ports 80 and 443, no credentials in the URL.
- **The host is resolved once, every address it resolved to is checked, and the connection goes to
  the checked address**: the URL's host is replaced by it, with the name kept for the `Host` header
  and, over TLS, for SNI and certificate verification. DNS rebinding can't swap the address between
  the check and the connect, because nothing asks DNS again. One bad address among several refuses
  the whole name.
- Refused: loopback, private, link-local (all of `169.254.0.0/16`, which includes the cloud metadata
  service), carrier-grade NAT, multicast, reserved and unspecified, in IPv4 and IPv6, including
  IPv4-mapped IPv6 and the tunnel forms that wrap an IPv4 address (6to4, NAT64, Teredo).
- **Unusual spellings are normalised before the check** (`2130706433`, `0x7f.1`, `127.1`, octal,
  a trailing dot), by the same rule a URL parser uses, so the check sees the address a connection
  would reach. A host whose last label is a number but isn't an address is refused.
- At most `LINK_MAX_REDIRECTS` (5), and **each redirect is parsed, resolved and checked as a new
  URL**, including its scheme and port. We follow redirects ourselves; the client never does.
- A connect timeout and one whole-fetch deadline (`LINK_FETCH_TIMEOUT_S`, 10 s), so a body that
  trickles in forever is cut off. A size limit (`LINK_MAX_BYTES`, 2 MB) counted on the body **after
  decompression**, read as a stream, so a gzip bomb stops at the limit. Only HTML, XHTML and plain
  text are read; the type is judged from the headers before the body is.
- Page scripts never run (nothing executes a page), and no cookies are sent or kept: a fresh client
  per fetch, its jar cleared after every hop, `trust_env=False` so no proxy or certificate setting
  leaks in from the environment.
- One retry on a dropped connection or a 5xx; after that it is a failure, not a refusal.
- **Logs show the host of a URL and nothing more.** The path and query count as message content.
- `LINK_ALLOW_PRIVATE_HOSTS` exempts named hosts (and any port) so the E2E stack can read its own
  fixture page server. The application refuses to start with it set in production.

**HTTP client:** `httpx`, already a dependency, async, with streaming responses and an `sni_hostname`
extension that lets us connect to an address and still verify the name.

**No headless browser.** It would run a stranger's scripts inside our network, turn every link into
a much larger attack surface, and cost memory a 2 GB host doesn't have. A page that only renders in
a browser is saved as *partial* (S4.7): what's in the HTML, and the person's own words.

## Alternatives considered

- **A deny-list of hostnames:** names resolve to anything, so only the resolved address counts.
- **Letting the client follow redirects:** it would check nothing on the second hop.
- **Resolving with the client and checking separately:** a second lookup at connect time is the
  rebinding window this design closes.
- **An egress firewall alone:** the host's own security group lets everything out (it must reach the
  model providers), and the same rules have to hold on a host that isn't AWS. The firewall is a
  second layer we don't have, so the application is the layer.

## Consequences

Some legitimate links are refused: a page on a non-standard port, and any site that only answers on
a private address. That is the intended trade. Every refusal has a stable rule code the glass box
shows. The fetcher reads the page a person pasted, nothing more, and its limits are settings.
