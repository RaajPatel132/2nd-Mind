"""Cut a page's text into chunks of about ``tokens`` tokens, with overlap (S4.7).

Chunks follow paragraphs and sentences, not fixed offsets, so a chunk reads as a unit and a
retrieved snippet makes sense on its own. Tokens are estimated from words (about 1.3 a word):
close enough to bound a chunk, and free of any model.
"""

import re
from dataclasses import dataclass

TOKENS_PER_WORD = 1.33
_SENTENCE = re.compile(r"(?<=[.!?…])\s+(?=[A-Z0-9\"'“(])")


@dataclass(frozen=True, slots=True)
class Chunk:
    position: int
    text: str

    @property
    def words(self) -> int:
        return len(self.text.split())


def estimate_tokens(text: str) -> int:
    return round(len(text.split()) * TOKENS_PER_WORD)


def _units(text: str, limit_words: int) -> list[str]:
    """Paragraphs, with any that are too long for a chunk split at sentence ends (and, failing
    that, at a word count)."""
    out: list[str] = []
    for paragraph in (p.strip() for p in text.split("\n\n")):
        if not paragraph:
            continue
        if len(paragraph.split()) <= limit_words:
            out.append(paragraph)
            continue
        for sentence in _SENTENCE.split(paragraph):
            words = sentence.split()
            while len(words) > limit_words:
                out.append(" ".join(words[:limit_words]))
                words = words[limit_words:]
            if words:
                out.append(" ".join(words))
    return out


def _tail(paragraphs: list[str], limit_words: int) -> tuple[str, int]:
    """The last sentences of a chunk, up to ``limit_words`` words: what the next chunk repeats."""
    sentences = _SENTENCE.split("\n\n".join(paragraphs))
    keep: list[str] = []
    count = 0
    for sentence in reversed(sentences):
        words = len(sentence.split())
        if count + words > limit_words:
            break
        keep.insert(0, sentence)
        count += words
    return " ".join(keep), count


def chunk_text(
    text: str, *, tokens: int = 300, max_chunks: int = 40, overlap: float = 0.15
) -> list[Chunk]:
    """Chunks of about ``tokens`` tokens, each starting with the last sentences of the one before
    it (about ``overlap`` of its size), at most ``max_chunks``. Empty text gives no chunks."""
    target = max(int(tokens / TOKENS_PER_WORD), 20)
    chunks: list[list[str]] = []
    current: list[str] = []
    size = 0
    fresh = 0  # words in `current` that are new, not carried over
    for unit in _units(text, target):
        words = len(unit.split())
        if current and size + words > target:
            chunks.append(current)
            carry, carried = _tail(current, int(target * overlap))
            current, size, fresh = ([carry] if carry else []), carried, 0
        current.append(unit)
        size += words
        fresh += words
    if current and fresh:
        chunks.append(current)
    return [Chunk(i, "\n\n".join(c)) for i, c in enumerate(chunks[:max_chunks])]
