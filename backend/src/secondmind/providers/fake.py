"""A deterministic, scriptable fake provider (S1.7).

Used by every unit, integration and E2E test by default, and by local dev when no provider
key is set. Responses are keyed by step and input: a test scripts outcomes per rule, and the
first matching rule answers. A rule's outcomes are consumed in order and the last one repeats,
so "fail twice, then succeed" is one rule. Unscripted calls get a deterministic default.
Usage is plausible (~4 characters per token).
"""

import asyncio
import hashlib
import itertools
import math
import re
import struct
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ValidationError

from secondmind.providers.contract import (
    AdapterEmbedding,
    AdapterReply,
    AdapterRequest,
    AdapterStreamEvent,
    AdapterStructured,
    ChatMessage,
    RawUsage,
    StreamEnd,
    TextDelta,
    ToolCall,
)
from secondmind.providers.errors import ProviderError, ProviderErrorKind

DEFAULT_EMBEDDING_DIM = 1536
_TOKEN_RE = re.compile(r"\S+\s*|\s+")


@dataclass(frozen=True, slots=True)
class FakeOutcome:
    """One scripted response. Exactly what the fake does for a matching call."""

    text: str | None = None
    structured: dict[str, Any] | None = None
    tool_calls: tuple[ToolCall, ...] = ()
    error: ProviderErrorKind | None = None
    fail_after_tokens: int | None = None
    delay_s: float = 0.0


@dataclass(slots=True)
class FakeRule:
    outcomes: list[FakeOutcome]
    step: str | None = None
    contains: str | None = None
    after_tool_result: bool | None = None
    calls: int = 0

    def matches(self, request: AdapterRequest) -> bool:
        if self.step is not None and self.step != request.step:
            return False
        last = request.messages[-1] if request.messages else None
        if self.after_tool_result is not None:
            is_tool = last is not None and last.role == "tool"
            if is_tool != self.after_tool_result:
                return False
        if self.contains is not None:
            haystack = " ".join(m.content for m in request.messages if m.role == "user")
            if self.contains.lower() not in haystack.lower():
                return False
        return True

    def next_outcome(self) -> FakeOutcome:
        outcome = self.outcomes[min(self.calls, len(self.outcomes) - 1)]
        self.calls += 1
        return outcome


# Answers an unscripted structured call for one step (the offline "brain" in fake mode).
Responder = Callable[[AdapterRequest], dict[str, Any] | None]
# Answers an unscripted text call for one step (None: the canned default reply).
TextResponder = Callable[[AdapterRequest], str | None]


@dataclass(slots=True)
class FakeScript:
    rules: list[FakeRule] = field(default_factory=list)
    responders: dict[str, Responder] = field(default_factory=dict)
    text_responders: dict[str, TextResponder] = field(default_factory=dict)

    def add(
        self,
        *outcomes: FakeOutcome,
        step: str | None = None,
        contains: str | None = None,
        after_tool_result: bool | None = None,
    ) -> FakeRule:
        if not outcomes:
            raise ValueError("a fake rule needs at least one outcome")
        rule = FakeRule(
            outcomes=list(outcomes),
            step=step,
            contains=contains,
            after_tool_result=after_tool_result,
        )
        self.rules.append(rule)
        return rule

    def match(self, request: AdapterRequest) -> FakeOutcome | None:
        for rule in self.rules:
            if rule.matches(request):
                return rule.next_outcome()
        return None

    def respond(self, request: AdapterRequest) -> dict[str, Any] | None:
        responder = self.responders.get(request.step)
        return None if responder is None else responder(request)

    def respond_text(self, request: AdapterRequest) -> str | None:
        responder = self.text_responders.get(request.step)
        return None if responder is None else responder(request)


def estimate_tokens(text: str) -> int:
    return max(1, math.ceil(len(text) / 4)) if text else 0


def default_reply(request: AdapterRequest) -> str:
    said = next((m.content for m in reversed(request.messages) if m.role == "user"), "")
    said = " ".join(said.split())
    if len(said) > 200:
        said = said[:199] + "…"
    return (
        f"Hello! I'm running on the offline fake provider, so this reply is canned. You said: \""
        f'{said}". Add an ANTHROPIC_API_KEY or OPENAI_API_KEY to get real answers.'
    )


class FakeProvider:
    """Implements ``ProviderAdapter`` with no network access."""

    def __init__(
        self,
        name: str = "fake",
        *,
        script: FakeScript | None = None,
        token_delay_s: float = 0.0,
        embedding_dim: int = DEFAULT_EMBEDDING_DIM,
    ) -> None:
        self._name = name
        self.script = script or FakeScript()
        self._token_delay_s = token_delay_s
        self._embedding_dim = embedding_dim
        self.requests: list[AdapterRequest] = []
        self.embed_calls: list[list[str]] = []
        self._seen_prefixes: set[str] = set()

    @property
    def name(self) -> str:
        return self._name

    async def stream_chat(self, request: AdapterRequest) -> AsyncIterator[AdapterStreamEvent]:
        outcome = await self._begin(request)
        text = outcome.text if outcome and outcome.text is not None else None
        if text is None and outcome is None:
            text = self.script.respond_text(request)
        text = text if text is not None else default_reply(request)
        tool_calls = list(outcome.tool_calls) if outcome else []
        if tool_calls and outcome and outcome.text is None:
            text = ""
        pieces = _TOKEN_RE.findall(text)
        for index, piece in enumerate(pieces):
            if (
                outcome
                and outcome.fail_after_tokens is not None
                and index >= outcome.fail_after_tokens
            ):
                raise ProviderError(
                    ProviderErrorKind.CONNECTION, "fake stream dropped", provider=self._name
                )
            if self._token_delay_s:
                await asyncio.sleep(self._token_delay_s)
            yield TextDelta(piece)
        yield StreamEnd(self._reply(request, text, tool_calls))

    async def chat(self, request: AdapterRequest) -> AdapterReply:
        outcome = await self._begin(request)
        if outcome is None:
            text = self.script.respond_text(request)
            return self._reply(request, text if text is not None else default_reply(request), [])
        tool_calls = list(outcome.tool_calls)
        text = (
            outcome.text
            if outcome.text is not None
            else ("" if tool_calls else default_reply(request))
        )
        return self._reply(request, text, tool_calls)

    async def structured[T: BaseModel](
        self, request: AdapterRequest, schema: type[T]
    ) -> AdapterStructured[T]:
        outcome = await self._begin(request)
        data: dict[str, Any] = {}
        if outcome is not None and outcome.structured is not None:
            data = outcome.structured
        elif outcome is None:
            data = self.script.respond(request) or {}
        try:
            value = schema.model_validate(data)
        except ValidationError as exc:
            raise ProviderError(
                ProviderErrorKind.INVALID_OUTPUT,
                f"fake has no valid scripted output for {schema.__name__} on step "
                f"{request.step!r}: {exc.error_count()} validation error(s)",
                provider=self._name,
            ) from exc
        usage = self._usage(request, value.model_dump_json())
        return AdapterStructured(value=value, usage=usage)

    async def embed(
        self, model: str, texts: Sequence[str], dimensions: int | None = None
    ) -> AdapterEmbedding:
        self.embed_calls.append(list(texts))
        dim = dimensions or self._embedding_dim
        vectors = [_embedding(text, dim) for text in texts]
        usage = RawUsage(input_tokens=sum(estimate_tokens(t) for t in texts), output_tokens=0)
        return AdapterEmbedding(vectors=vectors, usage=usage)

    async def aclose(self) -> None:
        return None

    async def _begin(self, request: AdapterRequest) -> FakeOutcome | None:
        self.requests.append(request)
        outcome = self.script.match(request)
        if outcome is None:
            return None
        if outcome.delay_s:
            await asyncio.sleep(outcome.delay_s)
        if outcome.error is not None and outcome.fail_after_tokens is None:
            status = {
                ProviderErrorKind.RATE_LIMITED: 429,
                ProviderErrorKind.SERVER: 500,
                ProviderErrorKind.BAD_REQUEST: 400,
                ProviderErrorKind.AUTH: 401,
            }.get(outcome.error)
            raise ProviderError(
                outcome.error, "scripted failure", provider=self._name, status_code=status
            )
        return outcome

    def _reply(
        self, request: AdapterRequest, text: str, tool_calls: list[ToolCall]
    ) -> AdapterReply:
        output = text + "".join(c.model_dump_json() for c in tool_calls)
        return AdapterReply(
            text=text,
            tool_calls=tool_calls,
            stop_reason="tool_use" if tool_calls else "end_turn",
            usage=self._usage(request, output),
        )

    def _usage(self, request: AdapterRequest, output: str) -> RawUsage:
        """Plausible usage; a cache prefix seen before is reported as cached input."""
        total = _input_tokens(request)
        cached = 0
        prefix = request.cache_prefix
        if prefix:
            if prefix in self._seen_prefixes:
                cached = min(total, estimate_tokens(prefix))
            self._seen_prefixes.add(prefix)
        return RawUsage(
            input_tokens=total - cached,
            cached_input_tokens=cached,
            output_tokens=estimate_tokens(output),
        )


def _input_tokens(request: AdapterRequest) -> int:
    parts: list[str] = [request.full_system or ""]
    parts.extend(_message_text(m) for m in request.messages)
    parts.extend(t.model_dump_json() for t in request.tools)
    return estimate_tokens("".join(parts))


def _message_text(message: ChatMessage) -> str:
    return message.content + "".join(c.model_dump_json() for c in message.tool_calls)


_WORD_RE = re.compile(r"[a-z0-9]+")
# Words too common to say anything about what a sentence is about.
_STOPWORDS = frozenset(
    {
        "a", "an", "and", "are", "as", "at", "be", "but", "by", "did", "do", "does", "for",
        "from", "had", "has", "have", "i", "in", "is", "it", "its", "me", "my", "of", "on",
        "or", "our", "so", "that", "the", "their", "them", "then", "there", "they", "this",
        "to", "was", "we", "were", "what", "when", "where", "which", "who", "will", "with",
        "you", "your",
    }
)  # fmt: skip


def _features(text: str) -> list[str]:
    """Lower-cased word tokens (lightly singularised) and their bigrams, minus stopwords."""
    words = [_singular(w) for w in _WORD_RE.findall(text.lower()) if w not in _STOPWORDS]
    return words + [f"{a} {b}" for a, b in itertools.pairwise(words)]


def _singular(word: str) -> str:
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def _embedding(text: str, dim: int) -> list[float]:
    """A feature-hashed bag of words (S3.1): each token and bigram adds ±1 at a hashed index,
    so sentences that share words end up close together. Deterministic; a text with no
    words falls back to a unit vector derived from its hash."""
    vector = [0.0] * dim
    for feature in _features(text):
        h = int.from_bytes(hashlib.blake2b(feature.encode(), digest_size=8).digest(), "big")
        vector[h % dim] += 1.0 if (h >> 40) & 1 else -1.0
    if not any(vector):
        values: list[float] = []
        counter = 0
        while len(values) < dim:
            digest = hashlib.sha256(f"{counter}:{text}".encode()).digest()
            values.extend(v / 2**31 for v in struct.unpack(">8i", digest))
            counter += 1
        vector = values[:dim]
    norm = math.sqrt(sum(v * v for v in vector)) or 1.0
    return [v / norm for v in vector]
