# ADR-0045: Delivery and link previews

- **Status:** accepted
- **Date:** 2026-10-02
- **Amends:** ADR-0034 (the web tier's configuration)

## Context

Two things about how the site reaches a browser were wrong. The bundle budget
(`npm run check:design`) is measured gzipped (160 KB against a 200 KB limit), but the web tier
served the script raw: 529 KB on the wire, about 3.3 times the budget's own figure, to visitors
who are a long way from the host. And a shared link previewed as a bare "2nd Mind": the page had
no description and no preview tags. Preview tags need an absolute image address, and the domain
is not known when the image is built (ADR-0035 leaves it to a variable).

## Decision

**Compress the static site, nothing else.** `gzip on` is set in the three static locations of
`frontend/nginx/default.conf.template` (`/`, `/assets/`, `/design`) and nowhere else: not on the
server as a whole and not in the API's location. API responses carry one person's data (BREACH)
and the turn stream must not be held back to be compressed. Fonts and images are already
compressed and are not in `gzip_types`.

**The page names itself.** `index.html` carries a description, Open Graph and Twitter tags and
the theme colour. Its image and URL tags contain the placeholder `__SITE_ORIGIN__`, which nginx
replaces (`sub_filter`, `text/html` only) with `$site_origin`. That value comes from a `map` over
`X-Forwarded-Proto` and `Host` and is non-empty only when both match a strict pattern (scheme
`http`/`https`; host of letters, digits, dots and hyphens, with an optional port). Anything else
gives an empty origin and the tags stay relative, so a crafted header can never put markup into
the page. Behind Caddy the scheme arrives in `X-Forwarded-Proto`; with no proxy it is nginx's own.

**Checked, not trusted.** `backend/tests/unit/test_web_delivery.py` pins both rules in the
template, and `frontend/e2e/delivery.spec.ts` checks the running stack: the script comes back
gzipped, the API's responses do not, and the tags carry the origin the request used.
`check:design` keeps the page's `theme-color` equal to the canvas token.

## Alternatives considered

- **A fixed origin in the build** (an environment variable at image build): the image would be
  tied to one domain, against ADR-0035's "built to move", and a rehearsal would advertise the
  wrong address.
- **Relative tags only:** some crawlers ignore a relative image address, which is the one thing
  that makes the preview worth having.
- **Compress at the edge (Caddy):** it works, but the budget and the web tier live together, and
  the rehearsal stack and a move to another host (row M) would each need it set up again.
- **Pre-compressed files (`gzip_static`):** a little less CPU for a little more build. Files are
  cached for a year and the host is quiet, so on-the-fly level 6 is enough.

## Consequences

First-visit transfer drops to about a third and the budget now describes what is delivered.
`index.html` is rewritten per request, so it is sent chunked and never cached (it already was
`no-cache`). The placeholder is a contract between two files: the unit test fails if either side
drifts. Revisit if the page is ever served from a CDN, which would need its own origin setting.
