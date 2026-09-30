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

__all__ = [
    "ACCEPTED_TYPES",
    "FetchFailedError",
    "FetchPolicy",
    "FetchedPage",
    "Fetcher",
    "Resolver",
    "SafeFetcher",
    "system_resolver",
]
