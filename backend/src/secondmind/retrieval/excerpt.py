"""The part of a saved page's passage to show (S4.7): the sentences that answer the question's
words, not just the start of the passage. Display only: the answer step is given the whole
passage."""

import re

_WORD = re.compile(r"[\w']+")
_STOP = frozenset(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "but",
        "by",
        "did",
        "do",
        "does",
        "for",
        "from",
        "had",
        "has",
        "have",
        "how",
        "i",
        "in",
        "is",
        "it",
        "its",
        "me",
        "my",
        "of",
        "on",
        "or",
        "she",
        "so",
        "that",
        "the",
        "their",
        "them",
        "then",
        "there",
        "they",
        "this",
        "to",
        "was",
        "we",
        "were",
        "what",
        "when",
        "where",
        "which",
        "who",
        "why",
        "will",
        "with",
        "you",
        "your",
        "about",
        "said",
        "say",
        "tell",
    ]
)
_MIN_LEN = 3
_ELLIPSIS = "…"


def _terms(question: str) -> set[str]:
    return {w for w in _WORD.findall(question.lower()) if len(w) >= _MIN_LEN and w not in _STOP}


def _same(word: str, term: str) -> bool:
    """A word matches its own forms: "lavender" finds "lavenders", "spray" finds "sprays"."""
    return word == term or word.startswith(term) or (len(word) > _MIN_LEN and term.startswith(word))


def _matching(sentence: str, terms: set[str]) -> set[str]:
    words = {w.lower() for w in _WORD.findall(sentence)}
    return {t for t in terms if any(_same(w, t) for w in words)}


def _scores(sentences: list[str], terms: set[str]) -> list[float]:
    """Each sentence's share of the question's words, a rarer word counting for more: a word in
    every sentence says nothing about which one answers."""
    found = [_matching(s, terms) for s in sentences]
    return [sum(1 / sum(t in f for f in found) for t in mine) for mine in found]


def excerpt(text: str, question: str, max_chars: int = 240) -> str:
    """At most ``max_chars`` of ``text`` (whitespace folded), centred on the sentences that share
    the most words with ``question``, with an ellipsis where it was cut."""
    flat = " ".join(text.split())
    if len(flat) <= max_chars:
        return flat
    sentences = re.split(r"(?<=[.!?])\s+", flat)
    terms = _terms(question)
    scores = _scores(sentences, terms)
    best = max(range(len(sentences)), key=lambda i: (scores[i], -i))
    chosen = [best]
    size = len(sentences[best])
    # Grow the window to the following sentences, then the preceding ones, while it fits.
    lo = hi = best
    while True:
        grew = False
        if hi + 1 < len(sentences) and size + 1 + len(sentences[hi + 1]) <= max_chars:
            hi += 1
            size += 1 + len(sentences[hi])
            grew = True
        if lo > 0 and size + 1 + len(sentences[lo - 1]) <= max_chars:
            lo -= 1
            size += 1 + len(sentences[lo])
            grew = True
        if not grew:
            break
    chosen = list(range(lo, hi + 1))
    out = " ".join(sentences[i] for i in chosen)
    if len(out) > max_chars:  # one long sentence: cut around its first matching word
        out = _around(out, terms, max_chars)
    lead = _ELLIPSIS + " " if chosen[0] > 0 else ""
    tail = " " + _ELLIPSIS if chosen[-1] < len(sentences) - 1 else ""
    return f"{lead}{out}{tail}"


def _around(sentence: str, terms: set[str], max_chars: int) -> str:
    lowered = sentence.lower()
    at = min((i for t in terms if (i := lowered.find(t)) >= 0), default=0)
    start = max(0, min(at - max_chars // 3, len(sentence) - max_chars))
    cut = sentence[start : start + max_chars].strip()
    return (
        (_ELLIPSIS + " " if start > 0 else "")
        + cut
        + (" " + _ELLIPSIS if start + max_chars < len(sentence) else "")
    )
