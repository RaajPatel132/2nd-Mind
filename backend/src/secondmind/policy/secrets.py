"""Deterministic secret pre-check (ADR-0021). Runs on the raw message before any model call.

It looks for secret-shaped content: a value after "password is", "PIN", "OTP", "one-time code"
and similar phrases, and strings shaped like API keys, tokens or private keys. A hit means the
extract call is skipped, so the secret never reaches a provider, and the stored turn keeps only
the redacted text. It errs towards refusing: "my password is the same as before" is a false
positive only when the word after "is" isn't a common word.
"""

import re
from dataclasses import dataclass

REDACTED = "[redacted]"

_VALUE = r"[\"'“]?(?P<value>[^\s\"'”,;]{2,})"
_PHRASE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "password",
        re.compile(
            r"\b(?:wi-?fi\s+)?(?:password|passcode|passphrase|passwd|pwd)"
            r"(?:\s+(?:for|to|of|on)\s+[\w .@'-]{1,40}?)?\s*(?:is|was|=|:|->)\s*" + _VALUE,
            re.IGNORECASE,
        ),
    ),
    (
        "pin",
        re.compile(
            r"\bPIN(?:\s+(?:number|code))?(?:\s+(?:for|to|of|on)\s+[\w .'-]{1,30}?)?"
            r"\s*(?:is|was|=|:)?\s*(?P<value>\d{4,8})\b",
            re.IGNORECASE,
        ),
    ),
    (
        "one_time_code",
        re.compile(
            r"\b(?:OTP|2FA(?:\s+code)?|one[- ]time\s+(?:pass(?:word|code)?|code)|"
            r"verification\s+code|security\s+code|login\s+code|auth(?:entication)?\s+code|CVV)"
            r"\s*(?:is|was|=|:)?\s*(?P<value>\d{3,10})\b",
            re.IGNORECASE,
        ),
    ),
    (
        "api_key",
        re.compile(
            r"\b(?:api[_ -]?key|secret[_ -]?key|access[_ -]?token|auth[_ -]?token|"
            r"bearer\s+token|private[_ -]?key|client[_ -]?secret)\s*(?:is|was|=|:)\s*" + _VALUE,
            re.IGNORECASE,
        ),
    ),
)

# Strings that are secrets by shape alone.
_SHAPE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("api_key", re.compile(r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_-]{20,}\b")),
    ("api_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("api_key", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}\b")),
    ("api_key", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{30,}\b")),
    ("api_key", re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}\b")),
    ("api_key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("api_key", re.compile(r"\b(?:pk|rk)_(?:live|test)_[0-9A-Za-z]{16,}\b")),
    ("token", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")),
    (
        "private_key",
        re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)"
        ),
    ),
)

# Words after "password is" that are not the password ("is the same", "is changed").
_NOT_A_VALUE = frozenset(
    {
        "the", "a", "an", "my", "your", "our", "same", "still", "not", "changed", "new", "old",
        "expired", "wrong", "correct", "saved", "stored", "somewhere", "written", "in", "on",
        "at", "set", "reset", "too", "very", "really", "hard", "easy", "strong", "weak", "long",
        "short", "that", "this", "it", "what", "different", "unchanged", "needed", "required",
    }
)  # fmt: skip


@dataclass(frozen=True, slots=True)
class SecretHit:
    kind: str
    start: int
    end: int


@dataclass(frozen=True, slots=True)
class SecretScan:
    hits: tuple[SecretHit, ...]
    redacted: str

    @property
    def found(self) -> bool:
        return bool(self.hits)

    @property
    def kinds(self) -> list[str]:
        return sorted({h.kind for h in self.hits})


def scan_secrets(text: str) -> SecretScan:
    """Find secret spans in ``text`` and return it with each span replaced by ``[redacted]``."""
    spans: list[SecretHit] = []
    for kind, pattern in _PHRASE_PATTERNS:
        for match in pattern.finditer(text):
            value = match.group("value")
            if value.lower().strip(".!?") in _NOT_A_VALUE:
                continue
            spans.append(SecretHit(kind, match.start("value"), match.end("value")))
    for kind, pattern in _SHAPE_PATTERNS:
        spans.extend(SecretHit(kind, m.start(), m.end()) for m in pattern.finditer(text))
    merged = _merge(spans)
    return SecretScan(hits=tuple(merged), redacted=_redact(text, merged))


def redact_values(text: str, values: list[str]) -> str:
    """Redact literal secret values (for spans a model labelled after the fact)."""
    for value in sorted({v for v in values if len(v.strip()) >= 2}, key=len, reverse=True):
        text = text.replace(value, REDACTED)
    return text


def _merge(spans: list[SecretHit]) -> list[SecretHit]:
    out: list[SecretHit] = []
    for span in sorted(spans, key=lambda s: (s.start, -s.end)):
        if out and span.start < out[-1].end:
            last = out[-1]
            out[-1] = SecretHit(last.kind, last.start, max(last.end, span.end))
        else:
            out.append(span)
    return out


def _redact(text: str, spans: list[SecretHit]) -> str:
    parts: list[str] = []
    cursor = 0
    for span in spans:
        parts.append(text[cursor : span.start])
        parts.append(REDACTED)
        cursor = span.end
    parts.append(text[cursor:])
    return "".join(parts)
