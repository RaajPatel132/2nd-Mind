"""Links (S4.6, S4.7): reading a page a person pasted, safely.

The pure rules (what a URL may point at, how a link is named and compared), saving the links in a
message, and reading a saved link are here. The HTTP client and the HTML extraction are in
``secondmind.links.adapters`` (enforced by an import contract), and the safe fetcher there is the
only way the app requests a URL a person gave.
"""

from secondmind.links.adapters.fetcher import FetchedPage, Fetcher, FetchFailedError
from secondmind.links.datablock import data_block, is_well_formed
from secondmind.links.digest import DigestOutput, DigestVars
from secondmind.links.model import (
    PENDING_REPLY,
    ChunkRow,
    FetchStatus,
    LinkKind,
    LinkSource,
)
from secondmind.links.reader import (
    DigestFn,
    LinkReader,
    LinkReading,
    LinkSourceStore,
    ReadResult,
    ReadSettings,
)
from secondmind.links.saver import LinkSaver, LinkSaveResult, SavedLink, SaveSettings, without_links
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
    "PENDING_REPLY",
    "ChunkRow",
    "DigestFn",
    "DigestOutput",
    "DigestVars",
    "FetchFailedError",
    "FetchRefusedError",
    "FetchStatus",
    "FetchedPage",
    "Fetcher",
    "LinkKind",
    "LinkReader",
    "LinkReading",
    "LinkSaveResult",
    "LinkSaver",
    "LinkSource",
    "LinkSourceStore",
    "ParsedUrl",
    "ReadResult",
    "ReadSettings",
    "SaveSettings",
    "SavedLink",
    "canonical_url",
    "check_resolved",
    "data_block",
    "extract_urls",
    "host_for_log",
    "is_well_formed",
    "parse_ipv4_spelling",
    "parse_url",
    "refusal_for",
    "site_name",
    "video_site",
    "without_links",
]
