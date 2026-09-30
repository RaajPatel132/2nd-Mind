"""Links (S4.6, S4.7): reading a page a person pasted, safely.

The pure rules (what a URL may point at, how a link is named and compared) are here. The HTTP
client and the HTML extraction are in ``secondmind.links.adapters`` (enforced by an import
contract), and the safe fetcher there is the only way the app requests a URL a person gave.
"""

from secondmind.links.urls import (
    ALLOWED_PORTS,
    ALLOWED_SCHEMES,
    FetchRefusedError,
    ParsedUrl,
    canonical_url,
    check_resolved,
    extract_urls,
    host_for_log,
    parse_ipv4_spelling,
    parse_url,
    refusal_for,
    site_name,
    video_site,
)

__all__ = [
    "ALLOWED_PORTS",
    "ALLOWED_SCHEMES",
    "FetchRefusedError",
    "ParsedUrl",
    "canonical_url",
    "check_resolved",
    "extract_urls",
    "host_for_log",
    "parse_ipv4_spelling",
    "parse_url",
    "refusal_for",
    "site_name",
    "video_site",
]
