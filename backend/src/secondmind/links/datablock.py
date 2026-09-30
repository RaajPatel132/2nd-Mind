"""Fetched text goes to a model only inside a delimited data block (S4.8, ADR-0037).

The delimiter depends on the text it wraps (a hash of it), so a page can't contain the closing
delimiter in advance: writing it would change the hash it depends on. On top of that, anything in
the text that looks like a delimiter is neutralised, so a forged one is inert in any case.
"""

import hashlib
import re

OPEN = "<<<DATA"
CLOSE = "<<<END DATA"
_LOOKALIKE = re.compile(r"(?i)<{2,}\s*(?:/?\s*)?(?:END\s*)?DATA\b[^\n>]*>{0,3}|>{3,}|<{3,}")


def neutralise(text: str) -> str:
    """Make anything that looks like a block delimiter harmless (a visible, different mark)."""
    return _LOOKALIKE.sub(lambda m: m.group(0).replace("<", "\u2039").replace(">", "\u203a"), text)


def nonce(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def data_block(label: str, text: str) -> str:
    """``text`` as quoted data: labelled, delimited, and introduced as material to read, never
    to obey."""
    clean = neutralise(text)
    tag = nonce(clean)
    safe_label = re.sub(r"[^a-z0-9 _.-]", "", label.lower())[:40]
    return f"{OPEN}:{tag} label={safe_label}>>>\n{clean}\n{CLOSE}:{tag}>>>"


def is_well_formed(block: str) -> bool:
    """One opening and one closing delimiter, with the same tag: what a forged block can't be."""
    opens = re.findall(rf"{re.escape(OPEN)}:([0-9a-f]{{12}}) label=", block)
    closes = re.findall(rf"{re.escape(CLOSE)}:([0-9a-f]{{12}})>>>", block)
    return len(opens) == 1 and opens == closes
