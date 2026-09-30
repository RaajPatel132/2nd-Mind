"""Video links (S4.7, FR-2.4): YouTube and Vimeo give a title, channel, description, duration
and thumbnail from oEmbed and the page's own metadata. There is no transcript: nothing here
reads a video. Every request goes through the safe fetcher, and a field the source doesn't give
stays empty."""

import json
import re
from dataclasses import dataclass
from urllib.parse import quote

from secondmind.links.adapters.extract import extract_html
from secondmind.links.adapters.fetcher import FetchedPage, Fetcher, FetchFailedError
from secondmind.links.urls import FetchRefusedError

OEMBED = {
    "youtube": "https://www.youtube.com/oembed?format=json&url={url}",
    "vimeo": "https://vimeo.com/api/oembed.json?url={url}",
}
_ISO_DURATION = re.compile(r"^P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$")


@dataclass(frozen=True, slots=True)
class VideoInfo:
    title: str = ""
    channel: str = ""
    description: str = ""
    duration_s: int | None = None
    thumbnail_url: str = ""
    published: str = ""
    author_url: str = ""

    @property
    def found(self) -> bool:
        return bool(self.title)


def parse_duration(value: object) -> int | None:
    """Seconds from ``PT1H2M3S`` (JSON-LD), ``3723``, or ``1:02:03``; None when unreadable."""
    if isinstance(value, int | float):
        return int(value) if value >= 0 else None
    text = str(value or "").strip()
    if text.isdigit():
        return int(text)
    match = _ISO_DURATION.match(text)
    if match and any(match.groups()):
        d, h, m, s = (int(g or 0) for g in match.groups())
        return d * 86_400 + h * 3_600 + m * 60 + s
    if re.fullmatch(r"\d{1,2}(?::\d{2}){1,2}", text):
        parts = [int(p) for p in text.split(":")]
        seconds = 0
        for part in parts:
            seconds = seconds * 60 + part
        return seconds
    return None


def parse_oembed(body: bytes) -> VideoInfo:
    try:
        data = json.loads(body.decode("utf-8", "replace"))
    except ValueError:
        return VideoInfo()
    if not isinstance(data, dict):
        return VideoInfo()
    return VideoInfo(
        title=str(data.get("title", "")).strip()[:300],
        channel=str(data.get("author_name", "")).strip()[:200],
        description=str(data.get("description", "")).strip()[:1000],
        duration_s=parse_duration(data.get("duration")),
        thumbnail_url=str(data.get("thumbnail_url", "")).strip()[:2048],
        author_url=str(data.get("author_url", "")).strip()[:2048],
    )


def parse_watch_page(page: FetchedPage) -> VideoInfo:
    """What the page's own metadata gives: Open Graph, and JSON-LD's VideoObject."""
    meta = extract_html(page.body, page.charset)
    return VideoInfo(
        title=meta.title,
        channel=meta.author,
        description=meta.description[:1000],
        duration_s=parse_duration(meta.duration),
        thumbnail_url=meta.image_url,
        published=meta.published,
    )


def merge(primary: VideoInfo, fallback: VideoInfo) -> VideoInfo:
    """``primary``'s fields, with ``fallback`` filling what it left empty."""
    return VideoInfo(
        title=primary.title or fallback.title,
        channel=primary.channel or fallback.channel,
        description=primary.description or fallback.description,
        duration_s=primary.duration_s if primary.duration_s is not None else fallback.duration_s,
        thumbnail_url=primary.thumbnail_url or fallback.thumbnail_url,
        published=primary.published or fallback.published,
        author_url=primary.author_url or fallback.author_url,
    )


async def read_video(fetcher: Fetcher, url: str, site: str) -> VideoInfo:
    """oEmbed first, then the watch page's metadata for what oEmbed leaves out (a description, a
    duration). Either may fail: what was found is returned, and nothing is found at all if both
    fail (the caller saves the link as partial)."""
    info = VideoInfo()
    endpoint = OEMBED[site].format(url=quote(url, safe=""))
    try:
        oembed = await fetcher.fetch(endpoint)
        if oembed.status_code == 200:
            info = parse_oembed(oembed.body)
    except (FetchRefusedError, FetchFailedError):
        pass
    if info.found and info.description and info.duration_s is not None:
        return info
    try:
        page = await fetcher.fetch(url)
        if page.status_code == 200:
            info = merge(info, parse_watch_page(page))
    except (FetchRefusedError, FetchFailedError):
        pass
    return info


__all__ = [
    "VideoInfo",
    "extract_html",
    "merge",
    "parse_duration",
    "parse_oembed",
    "parse_watch_page",
    "read_video",
]
