# ADR-0036: Link fetching and fetch safety

- **Status:** accepted (part 1, S4.6; part 2, S4.7)
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

## Part 2: reading the page (S4.7)

**Extraction** is in-house, on the standard library's HTML parser (`links/adapters/extract.py`): no
new dependency, nothing to audit, and it is the one place hidden text is decided. It reads Open
Graph and JSON-LD for title, site, author and date; finds the main text readability-style (an
`article` or `main`, else the block with the most prose and the least link text); drops scripts,
comments, hidden and furniture elements; and sets three signals: a paywall (text, class names or
structured data), a script-only page, and too short. A library such as trafilatura would do better on
odd layouts at the price of a dependency tree; the extractor is one module and can be swapped.

**Chunks** are about `LINK_CHUNK_TOKENS` (300) tokens, cut at paragraph and sentence ends, each one
starting with the last sentences of the one before, up to `LINK_MAX_CHUNKS`. They are stored as
`chunk` keys of the item (`memory_keys`, with a position), embedded with their title, so the soft
channel finds them with no new search code; a hit collapses to its item and hands over the passage.

**Status** follows the table in the sprint: `full` when the main text is found; `partial` for a
paywall, a script-only page, 401, 403 or 429, too large, too short, or a PDF (whatever there is is
kept, and "Add the text" completes it); `failed` for DNS, connection or timeout errors and a 5xx
after one retry; `refused` for a safety rule or a limit, with no request made.

**Video** (YouTube, Vimeo): title, channel, description, duration and thumbnail from oEmbed and the
page's own metadata; a field the source doesn't give stays empty; no transcript. Other video sites
are pages.

**The digest** (`digest@1`, Luna) writes the title, summary, tags and cue situations; its input is at
most 12,000 characters of the page in a data block. If the model's answer is invalid, or the
provider is down, the page's own title and description stand in and the link is still saved.

**Where it is written.** The read belongs to the turn that saved the link: the item's fill-in goes on
that turn's write log, the model calls on its ledger as the person's cost, and a `fetch` event is
appended to it. Undoing the turn removes the item. A chunk saved without a vector (the embedding
provider was down) is found by words until a later job embeds it.

**Cost of saving a link**, estimated from the price table (a live run, L1, confirms it): the digest is
about 3,500 input and 150 output tokens on Luna, about $0.0004; the chunk embeddings, about ten of 300
tokens at $0.02 per million, about $0.00006. About **$0.0005 a link**, against $0.006 for a chat turn.
