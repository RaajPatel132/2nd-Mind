"""Every rule about what a link may point at, as pure functions (S4.6, FR-2.8, NFR-1.2).

The public app must never be a way into anything private, or an open proxy. These functions decide,
before any request is made, whether a URL a person gave may be fetched, and normalise the many
spellings of an address so that the check sees what a connection would reach.
"""

import ipaddress
import re
from dataclasses import dataclass
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlsplit, urlunsplit

ALLOWED_SCHEMES = frozenset({"http", "https"})
ALLOWED_PORTS = frozenset({80, 443})
MAX_URL_CHARS = 2048
MAX_HOST_CHARS = 253

# Anything that looks like `scheme://...`: non-web schemes are found too, so they can be refused
# with a reason instead of being silently ignored.
_URL_RE = re.compile(r"(?i)\b(?:[a-z][a-z0-9+.-]{1,15}://|www\.)[^\s<>\"'`]+")


class FetchRefusedError(Exception):
    """A URL that must not be requested, or a request that has to stop. ``rule`` is a stable code
    (the glass box shows it, FR-4.6); ``message`` is what a person can be told."""

    def __init__(self, rule: str, message: str, detail: str = "") -> None:
        super().__init__(message)
        self.rule = rule
        self.message = message
        self.detail = detail  # e.g. the content type that was refused


@dataclass(frozen=True, slots=True)
class ParsedUrl:
    scheme: str
    host: str  # lower case, IDNA (ASCII); an IP literal without brackets
    port: int
    path_query: str  # path and query, never logged
    is_ip_literal: bool

    @property
    def default_port(self) -> bool:
        return self.port == (443 if self.scheme == "https" else 80)


def _trim(url: str) -> str:
    """Drop the punctuation a sentence puts after a link, but keep a closing bracket that matches
    one inside it (``.../Foo_(bar)``)."""
    while url:
        last = url[-1]
        if last in ".,;:!?'\"}>" or (
            last in ")]" and url.count(last) > url.count("(" if last == ")" else "[")
        ):
            url = url[:-1]
        else:
            break
    return url


def extract_urls(text: str) -> list[str]:
    """The URLs in a message, in order, without duplicates and without the punctuation around
    them. ``www.`` links get an ``https://`` scheme."""
    found: list[str] = []
    for match in _URL_RE.finditer(text):
        url = _trim(match.group(0))
        if url.lower().startswith("www."):
            url = "https://" + url
        if url and url not in found:
            found.append(url)
    return found


def host_for_log(url: str) -> str:
    """The host of a URL and nothing else: a path and query count as message content."""
    try:
        return (urlsplit(url).hostname or "").lower()[:MAX_HOST_CHARS]
    except ValueError:
        return ""


# ---------------------------------------------------------------- the spellings of an address


def _parse_ipv4_part(part: str) -> int | None:
    """One dot-separated number as a URL parser reads it: 0x hex, leading 0 octal, else decimal."""
    if part == "":
        return None
    try:
        if part[:2].lower() == "0x":
            return int(part[2:] or "0", 16)
        if len(part) > 1 and part[0] == "0":
            return int(part, 8)
        return int(part, 10)
    except ValueError:
        return None


def parse_ipv4_spelling(host: str) -> ipaddress.IPv4Address | None:
    """``2130706433``, ``0x7f.1``, ``127.1``, ``0177.0.0.1``, ``127.0.0.1.``: what an HTTP client
    turns into an address, normalised (the WHATWG "IPv4 parser"). None when it isn't one."""
    parts = host.rstrip(".").split(".") if host.endswith(".") else host.split(".")
    if not 1 <= len(parts) <= 4:
        return None
    numbers = [_parse_ipv4_part(p) for p in parts]
    if any(n is None for n in numbers):
        return None
    values = [n for n in numbers if n is not None]
    if any(n > 255 for n in values[:-1]):
        return None
    if values[-1] >= 256 ** (5 - len(values)):
        return None
    address = values[-1]
    for i, n in enumerate(values[:-1]):
        address += n * 256 ** (3 - i)
    return ipaddress.IPv4Address(address)


def _looks_numeric(host: str) -> bool:
    """A host whose last label is a number is an address in some spelling, never a name."""
    last = host.rstrip(".").rsplit(".", 1)[-1]
    return bool(re.fullmatch(r"(?i)0x[0-9a-f]*|[0-9]+", last))


_V6_MAPPED = ipaddress.IPv6Network("::ffff:0:0/96")
_V6_TUNNELS = (
    ipaddress.IPv6Network("64:ff9b::/96"),  # NAT64: embeds an IPv4 address
    ipaddress.IPv6Network("64:ff9b:1::/48"),
    ipaddress.IPv6Network("2002::/16"),  # 6to4
    ipaddress.IPv6Network("2001::/32"),  # Teredo
    ipaddress.IPv6Network("::/96"),  # IPv4-compatible (deprecated)
)
_CGNAT = ipaddress.IPv4Network("100.64.0.0/10")
_PRIVATE = tuple(
    ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7")
)


def unwrap(
    address: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    """An IPv4-mapped IPv6 address (``::ffff:127.0.0.1``) is the IPv4 address it wraps."""
    if isinstance(address, ipaddress.IPv6Address):
        mapped = address.ipv4_mapped
        if mapped is not None:
            return mapped
    return address


def refusal_for(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> tuple[str, str] | None:
    """Why a resolved address must not be connected to, as (rule, message); None when it is a
    public address. Loopback, private, link-local (all of 169.254.0.0/16, including the instance
    metadata service), carrier-grade NAT, multicast, reserved and unspecified, in both families."""
    address = unwrap(address)
    checks: tuple[tuple[bool, str, str], ...] = (
        (address.is_loopback, "loopback", "That's this server's own address."),
        (address.is_unspecified, "reserved_address", "That address is reserved."),
        (
            address.is_link_local,
            "link_local",
            "That's a link-local address (it includes cloud metadata services).",
        ),
        (address.is_multicast, "multicast", "That's a multicast address."),
        (
            isinstance(address, ipaddress.IPv4Address) and address in _CGNAT,
            "carrier_grade_nat",
            "That's a carrier-grade NAT address.",
        ),
        (
            any(address in n for n in _PRIVATE if n.version == address.version),
            "private_address",
            "That's a private address.",
        ),
        (
            isinstance(address, ipaddress.IPv6Address) and any(address in n for n in _V6_TUNNELS),
            "reserved_address",
            "That address is reserved.",
        ),
        (not address.is_global, "reserved_address", "That address is reserved."),
    )
    for hit, rule, message in checks:
        if hit:
            return rule, message
    return None


# ------------------------------------------------------------------ parsing a URL a person gave


def _host_of(hostname: str) -> tuple[str, bool]:
    """The host as it will be resolved, and whether it was an address. Raises for one that's
    malformed, a private address in any spelling, or the local host's own name."""
    host = hostname.lower()
    if "%" in host:
        raise FetchRefusedError("bad_url", "That isn't a valid web address.")
    literal: ipaddress.IPv4Address | ipaddress.IPv6Address | None = None
    if ":" in host:  # an IPv6 literal (urlsplit removed the brackets)
        try:
            literal = ipaddress.IPv6Address(host)
        except ValueError as exc:
            raise FetchRefusedError("bad_url", "That isn't a valid web address.") from exc
    else:
        literal = parse_ipv4_spelling(host)
        if literal is None and _looks_numeric(host):
            # `1.2.3.4.5` and the like: no resolver would read it as a name, and nor do we.
            raise FetchRefusedError("bad_url", "That isn't a valid web address.")
        if literal is None:
            host = _ascii_name(host)
    if literal is not None:
        reason = refusal_for(literal)
        if reason is not None:
            raise FetchRefusedError(*reason)
        return str(unwrap(literal)), True
    return host, False


def _ascii_name(host: str) -> str:
    try:
        name = host.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise FetchRefusedError("bad_url", "That isn't a valid web address.") from exc
    bare = name.rstrip(".")
    if len(name) > MAX_HOST_CHARS or not re.fullmatch(r"[a-z0-9._-]+", bare):
        raise FetchRefusedError("bad_url", "That isn't a valid web address.")
    if bare == "localhost" or bare.endswith(".localhost"):
        raise FetchRefusedError("loopback", "That's this server's own address.")
    return name


def parse_url(url: str, *, allow_hosts: frozenset[str] = frozenset()) -> ParsedUrl:
    """Split and check everything that can be checked without the network. Raises
    :class:`FetchRefusedError` for anything but a plain ``http(s)`` URL on port 80 or 443, with no
    credentials in it, to a host that isn't an address in a private range.

    ``allow_hosts`` is the test-only allowance (``LINK_ALLOW_PRIVATE_HOSTS``, development and test
    only): those exact hosts may be private and on any port, so the E2E stack can read its own
    fixture page server. Everything else still applies to them."""
    if len(url) > MAX_URL_CHARS:
        raise FetchRefusedError("url_too_long", "That link is too long.")
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in url):
        raise FetchRefusedError("bad_url", "That isn't a valid web address.")
    try:
        parts = urlsplit(url.strip())
        port = parts.port
        hostname = parts.hostname
    except ValueError as exc:
        raise FetchRefusedError("bad_url", "That isn't a valid web address.") from exc
    scheme = parts.scheme.lower()
    if scheme not in ALLOWED_SCHEMES:
        raise FetchRefusedError("scheme", "Only web links (http and https) are opened.")
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        raise FetchRefusedError("credentials", "Links with a user name or password aren't opened.")
    if not hostname:
        raise FetchRefusedError("bad_url", "That isn't a valid web address.")
    allowed = hostname.lower() in allow_hosts
    if port is not None and port not in ALLOWED_PORTS and not allowed:
        raise FetchRefusedError("port", "Only the standard web ports (80 and 443) are used.")
    if allowed:
        host, is_literal = hostname.lower(), parse_ipv4_spelling(hostname.lower()) is not None
    else:
        host, is_literal = _host_of(hostname)
    path_query = urlunsplit(("", "", parts.path or "/", parts.query, ""))
    return ParsedUrl(
        scheme, host, port or (443 if scheme == "https" else 80), path_query, is_literal
    )


def check_resolved(addresses: list[str]) -> None:
    """Every address a name resolved to, checked: one bad address refuses the whole name, so a
    name that mixes a public and a private address can't be used to reach the private one."""
    if not addresses:
        raise FetchRefusedError("dns", "That address didn't resolve.")
    for raw in addresses:
        try:
            address = ipaddress.ip_address(raw.split("%", 1)[0])
        except ValueError as exc:
            raise FetchRefusedError("dns", "That address didn't resolve to an address.") from exc
        reason = refusal_for(address)
        if reason is not None:
            raise FetchRefusedError(*reason)


# ------------------------------------------------------------------ canonical form, site, video

_TRACKING = re.compile(
    r"(?i)^(utm_.*|fbclid|gclid|dclid|msclkid|mc_cid|mc_eid|igshid|ref_src|spm|_hsenc|_hsmi)$"
)


def canonical_url(url: str) -> str:
    """The same link, however it was pasted: no fragment, no tracking parameters, a lower-case host
    without a default port, the query's parameters in order. It recognises a link saved before."""
    parts = urlsplit(url.strip())
    host = (parts.hostname or "").lower().rstrip(".")
    if host.startswith("www."):
        host = host[4:]
    scheme = parts.scheme.lower() or "https"
    port = parts.port
    netloc = host if port in (None, 80, 443) else f"{host}:{port}"
    path = quote(unquote(parts.path or "/"), safe="/:@!$&'()*+,;=-._~")
    if path != "/":
        path = path.rstrip("/") or "/"
    query = urlencode(
        sorted(
            (k, v)
            for k, v in parse_qsl(parts.query, keep_blank_values=True)
            if not _TRACKING.match(k)
        )
    )
    return urlunsplit(("https" if scheme in ("http", "https") else scheme, netloc, path, query, ""))


def site_name(host: str) -> str:
    """``www.example.com`` as ``example.com``: the name shown as a link's site."""
    host = host.lower().rstrip(".")
    return host[4:] if host.startswith("www.") else host


_YOUTUBE = {"youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be", "youtube-nocookie.com"}
_VIMEO = {"vimeo.com", "player.vimeo.com"}


def video_site(url: str) -> str | None:
    """``youtube`` or ``vimeo`` for a link to a video on either; other video sites are treated as
    pages (FR-2.4)."""
    host = site_name(host_for_log(url))
    path = urlsplit(url).path
    if host in _YOUTUBE:
        if host == "youtu.be" and len(path.strip("/")) > 0:
            return "youtube"
        if path.startswith(("/watch", "/shorts/", "/live/", "/embed/", "/v/")):
            return "youtube"
        return None
    if host in _VIMEO and re.match(r"^/(?:video/|channels/[^/]+/|groups/[^/]+/videos/)?\d+", path):
        return "vimeo"
    return None
