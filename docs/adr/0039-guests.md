# ADR-0039: Guests: device ids, per-address caps and expiry

- **Status:** accepted (S4.12)
- **Date:** 2026-10-01

## Context

FR-11.2, FR-13.4 and NFR-3.5. A recruiter clicks "Try the sample persona" once, from an office
network shared with colleagues who do the same. There is no sign-up. The site must still protect
the budget (a stranger is not a customer), keep one visitor's memory from another's, and not
keep anyone's content longer than the demo needs.

## Decision

**A guest is a user.** `POST /v1/guest` creates a user with tier `guest` and no email, gives it a
copy of the sample persona (ADR-0038), and sets the session cookie and a signed device cookie
(`HttpOnly`, `SameSite=Lax`, `Secure` on HTTPS) that lasts `DEVICE_COOKIE_TTL_DAYS`. A visitor who
returns with a valid device cookie continues as the same guest. The device token is signed with a
different purpose than the session token, so neither is accepted for the other, and a tampered one
is simply not believed (the visitor gets a new guest, which counts against the address cap).

**At most `GUEST_NEW_PER_IP_PER_DAY` (10) new guests per address per UTC day,** counted in Redis,
IPv6 per /64. The address comes from `X-Forwarded-For`, counted `TRUSTED_PROXY_HOPS` entries from
the right (Caddy, then nginx: 2), so a header the client sends can't choose the address. With fewer
entries than proxies we trust, the connection's own address is used. Over the cap a visitor gets a
clear refusal and a `Retry-After`; guests who already exist carry on. Ten, not two, because a hiring
panel often shares one office address; what actually limits spend is the next rule.

**Guests get a share of the day's cap.** All guests together may spend at most
`SPEND_CAP_GUEST_DAILY_USD` ($0.30 of the $0.50) a day. The total comes from the usage ledger
through a `SECURITY DEFINER` function that returns one number. After it, guests are read-only with a
new block reason, `guest_cap`, while signed-in people carry on. A guest turn is refused too when the
total can't be read. This amends ADR-0032, which had one cap for everyone.

**The other limits follow the tier:** the $0.75 lifetime allowance and the Auto model only, a lower
rate of turns (`RATE_TURNS_PER_MINUTE_GUEST`) and of fetches, and one link per message.

**Until the site opens, making a guest needs the access code too** (`GUESTS_OPEN` off), like every
other way in (decision 9 of the sprint).

**A guest may open a scratch memory:** an empty workspace of their own, from the switcher.

**Guest content expires.** A daily job empties the workspaces of guests older than `GUEST_TTL_DAYS`
(7): every table with content, children before parents. The row of the workspace stays, marked
`expired_at`, and is left out of every list. **The usage ledger is kept** (costs only, no content),
so the caps and the totals stay right: its turn reference becomes NULL when the turn is deleted
(`ON DELETE SET NULL (turn_id)`). A catalog test fails when a workspace-owned table is neither
emptied nor kept on purpose.

## Alternatives considered

- **Count guests by cookie alone:** a visitor clears cookies and is a new guest; only the address
  cap bounds that, which is why both exist.
- **A rate limit per address per minute:** it stops a burst, not a slow drip over a day.
- **Delete the workspace row on expiry:** it would take the ledger with it (the ledger points at
  turns of that workspace), and the day's totals would move backwards.

## Consequences

A guest's data lives at most `GUEST_TTL_DAYS` plus a day. The per-address count treats everyone
behind one address as one, including a mobile carrier's shared address, which is the trade-off the
limit of 10 tries to soften. Until S6, a person who wants to keep their own memory signs in.
