"""Readability-style extraction from an HTML page, on the standard library (ADR-0036 part 2).

Takes the bytes of a page and gives back its metadata and its main text. Nothing is executed and
nothing is fetched. What a visitor can't see is dropped: scripts and styles, HTML comments, anything
hidden by the ``hidden`` attribute, ``aria-hidden`` or an inline ``display:none`` or
``visibility:hidden``, and image alt text. Navigation, headers, footers, forms and obvious page
furniture are dropped too. Whatever is left is still *content*, to be treated as data by everything
downstream (ADR-0037): dropping hidden text is hygiene, not the defence.
"""

import json
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser

VOID = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "source",
        "track",
        "wbr",
    }
)
# Subtrees that never hold text a person reads.
SKIPPED = frozenset(
    {
        "script",
        "style",
        "noscript",
        "template",
        "svg",
        "canvas",
        "iframe",
        "object",
        "embed",
        "head",
    }
)
FURNITURE_TAGS = frozenset(
    {"nav", "header", "footer", "aside", "form", "button", "select", "dialog", "menu"}
)
FURNITURE_NAMES = re.compile(
    r"(?i)(?:^|[\s_-])(comment|comments|sidebar|footer|nav|navbar|menu|cookie|cookies|banner|share|"
    r"social|related|advert|ads|promo|newsletter|subscribe-box|breadcrumb|pagination|toolbar)(?:$|[\s_-])"
)
BLOCKS = frozenset(
    {
        "p",
        "div",
        "section",
        "article",
        "main",
        "li",
        "ul",
        "ol",
        "blockquote",
        "pre",
        "table",
        "tr",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "figcaption",
        "dd",
        "dt",
        "br",
    }
)
HEADINGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})
HIDDEN_STYLE = re.compile(
    r"(?i)display\s*:\s*none|visibility\s*:\s*hidden|font-size\s*:\s*0(?:px)?\b|opacity\s*:\s*0(?:\.0+)?\s*(?:;|$)"
)
PAYWALL_TEXT = re.compile(
    r"(?i)(subscribe to (?:continue|keep) reading|sign in to (?:continue )?read|"
    r"this (?:article|content) is (?:for|available to) (?:paid )?subscribers|"
    r"already a subscriber\?|create a free account to continue|log in to read the full)"
)
PAYWALL_NAMES = re.compile(r"(?i)(paywall|premium-content|subscriber-only|locked-content|regwall)")
MIN_WORDS = 40


@dataclass(slots=True)
class Node:
    tag: str
    attrs: dict[str, str]
    parent: "Node | None" = None
    children: "list[Node | str]" = field(default_factory=list)

    def hidden(self) -> bool:
        if "hidden" in self.attrs or self.attrs.get("aria-hidden", "").lower() == "true":
            return True
        return bool(HIDDEN_STYLE.search(self.attrs.get("style", "")))

    def names(self) -> str:
        return f"{self.attrs.get('class', '')} {self.attrs.get('id', '')}".strip()


class _Builder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("root", {})
        self.current = self.root
        self.meta: list[dict[str, str]] = []
        self.links: list[dict[str, str]] = []
        self.title = ""
        self._in_title = False
        self.json_ld: list[str] = []
        self._ld: list[str] | None = None
        self.noscript_words = 0
        self._noscript_depth = 0
        self.script_count = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "meta":
            self.meta.append(a)
            return
        if tag == "link":
            self.links.append(a)
            return
        if tag == "title":
            self._in_title = True
        if tag == "noscript":
            self._noscript_depth += 1
        if tag == "script":
            self.script_count += 1
            if a.get("type", "").lower() == "application/ld+json":
                self._ld = []
        if tag in VOID:
            return
        node = Node(tag, a, self.current)
        self.current.children.append(node)
        self.current = node

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        if tag == "noscript" and self._noscript_depth:
            self._noscript_depth -= 1
        if tag == "script" and self._ld is not None:
            self.json_ld.append("".join(self._ld))
            self._ld = None
        node = self.current
        while node.parent is not None and node.tag != tag:
            node = node.parent
        if node.tag == tag and node.parent is not None:
            self.current = node.parent

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
        if self._ld is not None:
            self._ld.append(data)
            return
        if self.current.tag in ("script", "style"):
            return
        if self._noscript_depth:
            self.noscript_words += len(data.split())
        self.current.children.append(data)

    def handle_comment(self, data: str) -> None:  # an HTML comment is never read
        return


def _meta(builder: _Builder, *names: str) -> str:
    wanted = {n.lower() for n in names}
    for m in builder.meta:
        key = (m.get("property") or m.get("name") or m.get("itemprop") or "").lower()
        if key in wanted and m.get("content", "").strip():
            return m["content"].strip()
    return ""


def _json_ld(builder: _Builder) -> dict[str, object]:
    """The first Article-like object in the page's JSON-LD blocks, flattened to what we read."""

    def walk(value: object) -> list[dict[str, object]]:
        if isinstance(value, list):
            return [o for v in value for o in walk(v)]
        if isinstance(value, dict):
            found = [value] if value.get("@type") else []
            return found + walk(value.get("@graph", []))
        return []

    for block in builder.json_ld:
        try:
            parsed = json.loads(block)
        except ValueError:
            continue
        for obj in walk(parsed):
            kind = str(obj.get("@type"))
            if re.search(r"(?i)article|blogposting|newsarticle|videoobject|webpage", kind):
                return obj
    return {}


def _author(value: object) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        return str(value.get("name", "")).strip()
    if isinstance(value, list):
        names = [_author(v) for v in value]
        return ", ".join(n for n in names if n)
    return ""


# ------------------------------------------------------------------ main content


def _text_of(node: Node, *, keep_furniture: bool = False) -> str:
    """The visible text of a subtree, with paragraph breaks at block elements."""
    out: list[str] = []

    def visit(n: Node) -> None:
        if n.tag in SKIPPED or n.hidden():
            return
        if not keep_furniture and (n.tag in FURNITURE_TAGS or FURNITURE_NAMES.search(n.names())):
            return
        block = n.tag in BLOCKS
        if block:
            out.append("\n\n" if n.tag in HEADINGS or n.tag == "p" else "\n")
        if n.tag == "li":
            out.append("- ")
        for child in n.children:
            if isinstance(child, str):
                out.append(child)
            else:
                visit(child)
        if block:
            out.append("\n\n" if n.tag in HEADINGS or n.tag == "p" else "\n")

    visit(node)
    return _tidy("".join(out))


def _tidy(text: str) -> str:
    text = text.replace("\xa0", " ")
    lines = [re.sub(r"[ \t\r\f\v]+", " ", line).strip() for line in text.split("\n")]
    paragraphs: list[str] = []
    blank = True
    for line in lines:
        if not line:
            blank = True
            continue
        if blank or not paragraphs:
            paragraphs.append(line)
        else:
            paragraphs[-1] += "\n" + line if line.startswith("- ") else " " + line
        blank = False
    deduped = [p for i, p in enumerate(paragraphs) if i == 0 or p != paragraphs[i - 1]]
    return "\n\n".join(deduped)


def _score(node: Node) -> float:
    """How much of a subtree is prose: paragraph text, less what is link text."""
    if node.tag in SKIPPED or node.hidden():
        return 0.0
    paragraph = 0
    link = 0

    def visit(n: Node, in_link: bool) -> None:
        nonlocal paragraph, link
        if (
            n.tag in SKIPPED
            or n.hidden()
            or n.tag in FURNITURE_TAGS
            or FURNITURE_NAMES.search(n.names())
        ):
            return
        for child in n.children:
            if isinstance(child, str):
                length = len(child.strip())
                if in_link:
                    link += length
                elif n.tag in ("p", "li", "blockquote", "pre", "td", "dd") or n.tag in HEADINGS:
                    paragraph += length
            else:
                visit(child, in_link or child.tag == "a")

    visit(node, node.tag == "a")
    density = link / (paragraph + link) if paragraph + link else 1.0
    return paragraph * (1 - density)


def _candidates(root: Node) -> list[Node]:
    found: list[Node] = []

    def visit(n: Node) -> None:
        if (
            n.tag in ("article", "main")
            or n.attrs.get("role") == "main"
            or n.attrs.get("itemprop") == "articleBody"
        ) or (
            n.tag in ("div", "section")
            and re.search(
                r"(?i)(post-content|entry-content|article-body|article__body|story-body|post-body|content-body|markdown-body)",
                n.names(),
            )
        ):
            found.append(n)
        for child in n.children:
            if isinstance(child, Node):
                visit(child)

    visit(root)
    return found


def _body(root: Node) -> Node:
    def find(n: Node) -> Node | None:
        if n.tag == "body":
            return n
        for child in n.children:
            if isinstance(child, Node) and (hit := find(child)) is not None:
                return hit
        return None

    return find(root) or root


@dataclass(frozen=True, slots=True)
class ExtractedPage:
    title: str
    description: str
    site: str
    author: str
    published: str
    canonical_url: str
    language: str
    text: str
    word_count: int
    method: str  # article | main | largest-block | body | noscript | plain | none
    paywall: bool
    script_only: bool
    image_url: str = ""
    duration: str = ""  # a video's length as the page states it (ISO 8601, or seconds)

    @property
    def too_short(self) -> bool:
        return self.word_count < MIN_WORDS


def decode_body(body: bytes, declared: str | None = None) -> str:
    """Text from bytes: the declared charset, else one named in the page's own head, else UTF-8,
    else Latin-1 with nothing lost."""
    candidates = [declared] if declared else []
    sniff = re.search(rb"(?i)<meta[^>]+charset\s*=\s*[\"']?\s*([a-z0-9_-]+)", body[:4096])
    if sniff:
        candidates.append(sniff.group(1).decode("ascii", "ignore"))
    candidates += ["utf-8"]
    for name in candidates:
        try:
            return body.decode(name or "utf-8")
        except (LookupError, UnicodeDecodeError):
            continue
    return body.decode("latin-1")


def extract_plain(text: str) -> ExtractedPage:
    text = _tidy(text)
    first = next((line for line in text.split("\n") if line.strip()), "")
    return ExtractedPage(
        title=first[:120],
        description="",
        site="",
        author="",
        published="",
        canonical_url="",
        language="",
        text=text,
        word_count=len(text.split()),
        method="plain",
        paywall=False,
        script_only=False,
    )


def extract_html(body: bytes, declared_charset: str | None = None) -> ExtractedPage:
    html = decode_body(body, declared_charset)
    builder = _Builder()
    builder.feed(html)
    builder.close()
    ld = _json_ld(builder)

    title = (
        _meta(builder, "og:title", "twitter:title") or re.sub(r"\s+", " ", builder.title).strip()
    )
    if not title and ld.get("headline"):
        title = str(ld["headline"]).strip()
    description = (
        _meta(builder, "og:description", "description", "twitter:description")
        or str(ld.get("description", "")).strip()
    )
    author = _meta(builder, "author", "article:author", "parsely-author") or _author(
        ld.get("author")
    )
    published = (
        _meta(
            builder,
            "article:published_time",
            "og:article:published_time",
            "datepublished",
            "date",
            "pubdate",
        )
        or str(ld.get("datePublished", "")).strip()
    )
    canonical = next(
        (
            link.get("href", "")
            for link in builder.links
            if "canonical" in link.get("rel", "").lower().split()
        ),
        "",
    )
    site = _meta(builder, "og:site_name", "application-name")
    language = next(
        (
            c.attrs.get("lang", "")
            for c in builder.root.children
            if isinstance(c, Node) and c.tag == "html"
        ),
        "",
    )
    image = _meta(builder, "og:image", "twitter:image")
    duration = _meta(builder, "video:duration", "og:video:duration") or str(ld.get("duration", ""))

    body_node = _body(builder.root)
    method = "body"
    chosen = body_node
    candidates = [c for c in _candidates(builder.root) if _score(c) > 0]
    if candidates:
        chosen = max(candidates, key=_score)
        method = (
            "article"
            if chosen.tag == "article"
            else "main"
            if chosen.tag == "main" or chosen.attrs.get("role") == "main"
            else "largest-block"
        )
    text = _text_of(chosen)
    if len(text.split()) < MIN_WORDS and chosen is not body_node:  # the container was too narrow
        whole = _text_of(body_node)
        if len(whole.split()) > len(text.split()):
            text, method = whole, "body"
    if len(text.split()) < MIN_WORDS:  # a page that says "enable JavaScript" may hold its text here
        fallback = _noscript_text(builder.root)
        if len(fallback.split()) > len(text.split()):
            text, method = fallback, "noscript"
    if not text:
        method = "none"

    names = " ".join(n.names() for n in _walk(builder.root))
    walled = bool(PAYWALL_TEXT.search(text)) or bool(PAYWALL_NAMES.search(names))
    if (
        ld.get("isAccessibleForFree") in (False, "False", "false")
        or _meta(builder, "article:content_tier") == "locked"
    ):
        walled = True
    script_only = (
        len(text.split()) < MIN_WORDS
        and (builder.script_count >= 1)
        and builder.noscript_words < MIN_WORDS
    )
    return ExtractedPage(
        title=title[:300],
        description=description[:600],
        site=site[:120],
        author=author[:200],
        published=published[:40],
        canonical_url=canonical[:2048],
        language=language[:16],
        text=text,
        word_count=len(text.split()),
        method=method,
        paywall=walled,
        script_only=script_only,
        image_url=image[:2048],
        duration=duration[:40],
    )


def _noscript_text(root: Node) -> str:
    parts = [
        _text_of(child)
        for n in _walk(root)
        if n.tag == "noscript"
        for child in n.children
        if isinstance(child, Node)
    ]
    return _tidy("\n\n".join(p for p in parts if p))


def _walk(root: Node) -> list[Node]:
    out: list[Node] = []
    stack = [root]
    while stack:
        n = stack.pop()
        out.append(n)
        stack.extend(c for c in n.children if isinstance(c, Node))
    return out
