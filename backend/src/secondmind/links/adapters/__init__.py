"""The link adapters: the HTTP client and HTML extraction live only here (ADR-0036)."""

from secondmind.links.adapters.fetcher import (
    ACCEPTED_TYPES,
    FetchedPage,
    Fetcher,
    FetchFailedError,
    FetchPolicy,
    Resolver,
    SafeFetcher,
    system_resolver,
)
from secondmind.links.adapters.store import SqlLinkStore
from secondmind.links.adapters.tables import WORKSPACE_OWNED_LINK_TABLES, LinkSourceRow

__all__ = [
    "ACCEPTED_TYPES",
    "WORKSPACE_OWNED_LINK_TABLES",
    "FetchFailedError",
    "FetchPolicy",
    "FetchedPage",
    "Fetcher",
    "LinkSourceRow",
    "Resolver",
    "SafeFetcher",
    "SqlLinkStore",
    "system_resolver",
]
