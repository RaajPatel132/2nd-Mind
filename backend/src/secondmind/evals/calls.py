"""Every model call an eval run makes, metered (R.1): cost, latency and whether it came from the
response cache.

:class:`MeteredAdapter` wraps a provider adapter for the harness only (the app's own router is
built by ``providers.adapters.build_router`` and never sees it). It records one
:class:`CallRecord` per call in a shared :class:`CallLog`, and runs in one of three ways:

* **live:** the real adapter behind a :class:`ResponseCache`, keyed on the full normalised
  request (provider, model, parameters, prompt text, messages, tools, schema). A hit costs $0,
  is marked, and is left out of latency figures; a miss is paid for and stored. So a re-run
  after a fix pays only for the calls whose request changed.
* **fake:** the offline fake, costed at the fake's synthetic prices.
* **dry run:** the offline fake, but each call is costed as the live model it stands in for,
  from the request's size and the step's typical output length (:class:`Estimator`).

The cache lives in ``evals/runs/.cache/`` (gitignored). Failed calls that were probably billed
(a response that didn't parse, a timeout) are costed by estimate, so the spend total errs high.
"""

import hashlib
import json
import math
import os
import time
from collections import defaultdict
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from secondmind.config import ModelRef, PriceTable
from secondmind.providers import (
    AdapterEmbedding,
    AdapterReply,
    AdapterRequest,
    AdapterStreamEvent,
    AdapterStructured,
    ProviderAdapter,
    ProviderError,
    ProviderErrorKind,
    RawUsage,
    StreamEnd,
    TextDelta,
    ToolCall,
)

CACHE_DIR_NAME = ".cache"
# English prose and JSON run a little under 4 characters a token; estimate on the high side.
CHARS_PER_TOKEN = 3.5
# Output tokens a step typically writes, before any are observed in the cache.
TYPICAL_OUTPUT: dict[str, int] = {
    "intent": 60,
    "extract": 700,
    "resolve": 200,
    "enrich": 250,
    "reconcile": 120,
    "plan": 350,
    "rerank": 300,
    "correct": 150,
    "answer": 250,
    "judge": 300,
    "embed": 0,
    "ping": 1,
}
# Hidden reasoning tokens a reasoning model may add at each effort level (billed as output).
REASONING_ALLOWANCE: dict[str, int] = {"low": 40, "medium": 300, "high": 1000}
# Failures that were probably billed (the model ran), as opposed to refused requests.
_BILLED_FAILURES = frozenset({ProviderErrorKind.INVALID_OUTPUT, ProviderErrorKind.TIMEOUT})


@dataclass(frozen=True, slots=True)
class CallRecord:
    """One call as the harness saw it."""

    step: str
    provider: str
    model: str
    cost_usd: Decimal
    latency_ms: int
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    ttft_ms: int | None = None
    cache_hit: bool = False
    estimated: bool = False
    billed: bool = False
    failed: str | None = None
    # What the model returned: a structured value, or the text of a chat reply (for triage and
    # for recording replays, R.5). Never set for embeddings.
    output: Any = None

    @property
    def spent_usd(self) -> Decimal:
        """What this call cost for real: only a billed call that wasn't a cache hit."""
        return self.cost_usd if self.billed and not self.cache_hit else Decimal(0)

    def to_json(self) -> dict[str, Any]:
        return {
            "step": self.step,
            "provider": self.provider,
            "model": self.model,
            "cost_usd": float(self.cost_usd),
            "latency_ms": self.latency_ms,
            "ttft_ms": self.ttft_ms,
            "input_tokens": self.input_tokens,
            "cached_input_tokens": self.cached_input_tokens,
            "output_tokens": self.output_tokens,
            "cache_hit": self.cache_hit,
            "estimated": self.estimated,
            "billed": self.billed,
            "failed": self.failed,
            **({"output": self.output} if self.output is not None else {}),
        }


@dataclass(slots=True)
class CallLog:
    """Calls in the order they finished, shared by every adapter of a run."""

    records: list[CallRecord] = field(default_factory=list)

    def mark(self) -> int:
        return len(self.records)

    def since(self, mark: int) -> list[CallRecord]:
        return self.records[mark:]

    def add(self, record: CallRecord) -> None:
        self.records.append(record)


# ------------------------------------------------------------------ the response cache


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def request_material(request: AdapterRequest) -> dict[str, Any]:
    """Everything that decides a chat response, in a stable form."""
    return {
        "step": request.step,
        "model": request.model,
        "system": request.system,
        "cache_prefix": request.cache_prefix,
        "messages": [m.model_dump(mode="json") for m in request.messages],
        "max_output_tokens": request.max_output_tokens,
        "effort": request.effort,
        "tools": [t.model_dump(mode="json") for t in request.tools],
    }


class ResponseCache:
    """Model responses on disk, one JSON file per request key. Harness only."""

    def __init__(self, root: Path, *, read: bool = True) -> None:
        self._root = root
        self._read = read

    @staticmethod
    def key(provider: str, kind: str, material: Mapping[str, Any]) -> str:
        digest = hashlib.sha256(
            _canonical({"provider": provider, "kind": kind, **material}).encode("utf-8")
        )
        return digest.hexdigest()

    def _path(self, key: str) -> Path:
        return self._root / key[:2] / f"{key}.json"

    def get(self, key: str) -> dict[str, Any] | None:
        if not self._read:
            return None
        path = self._path(key)
        if not path.exists():
            return None
        value: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        return value

    def put(self, key: str, value: Mapping[str, Any]) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(_canonical(value), encoding="utf-8")
        tmp.replace(path)

    def output_stats(self) -> dict[tuple[str, str], float]:
        """Mean output tokens per (step, provider:model) over the cached responses."""
        seen: dict[tuple[str, str], list[int]] = defaultdict(list)
        if not self._root.exists():
            return {}
        for path in self._root.glob("*/*.json"):
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            meta = value.get("meta") or {}
            usage = value.get("usage") or {}
            if meta.get("step") and meta.get("ref") and "output_tokens" in usage:
                seen[(str(meta["step"]), str(meta["ref"]))].append(int(usage["output_tokens"]))
        return {k: sum(v) / len(v) for k, v in seen.items() if len(v) >= 3}


def _usage_json(usage: RawUsage) -> dict[str, int]:
    return {
        "input_tokens": usage.input_tokens,
        "cached_input_tokens": usage.cached_input_tokens,
        "output_tokens": usage.output_tokens,
    }


def _usage_of(raw: Mapping[str, Any]) -> RawUsage:
    return RawUsage(
        input_tokens=int(raw.get("input_tokens", 0)),
        cached_input_tokens=int(raw.get("cached_input_tokens", 0)),
        output_tokens=int(raw.get("output_tokens", 0)),
    )


# ------------------------------------------------------------------ dry-run estimates


class Estimator:
    """Estimated cost of a request on a given live model: input from the request's size, output
    from what the step typically writes (observed in the cache when there's enough of it)."""

    def __init__(
        self, prices: PriceTable, observed: Mapping[tuple[str, str], float] | None = None
    ) -> None:
        self._prices = prices
        self._observed = dict(observed or {})

    @staticmethod
    def input_tokens(request: AdapterRequest, schema: type[BaseModel] | None = None) -> int:
        chars = len(request.full_system or "")
        for m in request.messages:
            chars += len(m.content)
            if m.tool_calls:
                chars += len(_canonical([c.model_dump(mode="json") for c in m.tool_calls]))
        chars += sum(len(t.model_dump_json()) for t in request.tools)
        if schema is not None:
            chars += len(_canonical(schema.model_json_schema()))
        return math.ceil(chars / CHARS_PER_TOKEN) + 8 * len(request.messages)

    def output_tokens(self, step: str, ref: ModelRef, effort: str | None) -> int:
        base = self._observed.get((step, str(ref)))
        if base is None:
            base = TYPICAL_OUTPUT.get(step, 300)
            if effort and _reasons(ref):
                base += REASONING_ALLOWANCE.get(effort, 0)
        return math.ceil(base)

    def chat(
        self,
        request: AdapterRequest,
        ref: ModelRef,
        schema: type[BaseModel] | None = None,
    ) -> tuple[RawUsage, Decimal]:
        usage = RawUsage(
            input_tokens=self.input_tokens(request, schema),
            output_tokens=self.output_tokens(request.step, ref, request.effort),
        )
        return usage, self.cost(ref, usage)

    def embed(self, texts: Sequence[str], ref: ModelRef) -> tuple[RawUsage, Decimal]:
        usage = RawUsage(
            input_tokens=sum(math.ceil(len(t) / CHARS_PER_TOKEN) for t in texts), output_tokens=0
        )
        return usage, self.cost(ref, usage)

    def cost(self, ref: ModelRef, usage: RawUsage) -> Decimal:
        return self._prices.cost(
            ref,
            input_tokens=usage.input_tokens,
            cached_input_tokens=usage.cached_input_tokens,
            output_tokens=usage.output_tokens,
        )


def _reasons(ref: ModelRef) -> bool:
    """Models that think before answering by default (billed as output tokens)."""
    if ref.provider == "openai":
        return ref.model.startswith(("gpt-5", "gpt-6", "o"))
    if ref.provider == "anthropic":
        return not ref.model.startswith(("claude-haiku", "claude-3"))
    return False


# ------------------------------------------------------------------ the adapter wrapper


class MeteredAdapter:
    """A provider adapter that records every call, optionally through the response cache or as
    a dry-run estimate for ``targets`` (step -> the live model the call stands in for)."""

    def __init__(
        self,
        inner: ProviderAdapter,
        *,
        log: CallLog,
        prices: PriceTable,
        cache: ResponseCache | None = None,
        targets: Mapping[str, ModelRef] | None = None,
        estimator: Estimator | None = None,
        billed: bool = False,
    ) -> None:
        """``billed``: the inner adapter is a real provider (its calls cost money)."""
        self._inner = inner
        self._billed = billed
        self._log = log
        self._prices = prices
        self._cache = cache
        self._targets = dict(targets or {})
        self._estimator = estimator or Estimator(prices)

    @property
    def name(self) -> str:
        return self._inner.name

    @property
    def inner(self) -> ProviderAdapter:
        return self._inner

    def _ref(self, step: str, model: str) -> ModelRef:
        return self._targets.get(step) or ModelRef(provider=self._inner.name, model=model)

    @property
    def _dry(self) -> bool:
        return bool(self._targets)

    def _record(
        self,
        request_step: str,
        ref: ModelRef,
        usage: RawUsage,
        started: float,
        *,
        ttft: float | None = None,
        hit: bool = False,
        cost: Decimal | None = None,
        failed: str | None = None,
        output: Any = None,
    ) -> None:
        now = time.perf_counter()
        self._log.add(
            CallRecord(
                step=request_step,
                provider=ref.provider,
                model=ref.model,
                cost_usd=cost
                if cost is not None
                else self._prices.cost(
                    ref,
                    input_tokens=usage.input_tokens,
                    cached_input_tokens=usage.cached_input_tokens,
                    output_tokens=usage.output_tokens,
                ),
                latency_ms=round((now - started) * 1000),
                ttft_ms=None if ttft is None else round((ttft - started) * 1000),
                input_tokens=usage.input_tokens,
                cached_input_tokens=usage.cached_input_tokens,
                output_tokens=usage.output_tokens,
                cache_hit=hit,
                estimated=self._dry,
                billed=self._billed,
                failed=failed,
                output=output,
            )
        )

    def _failed(
        self,
        request: AdapterRequest,
        err: ProviderError,
        started: float,
        schema: type[BaseModel] | None = None,
    ) -> None:
        ref = self._ref(request.step, request.model)
        if err.kind in _BILLED_FAILURES:
            usage, cost = self._estimator.chat(request, ref, schema)
        else:
            usage, cost = RawUsage(input_tokens=0, output_tokens=0), Decimal(0)
        # The fixtures are synthetic, so the reply that broke the schema is worth keeping.
        self._record(
            request.step,
            ref,
            usage,
            started,
            cost=cost,
            failed=err.kind.value,
            output=_loaded(err.raw_output),
        )

    def _cost(
        self,
        request: AdapterRequest,
        ref: ModelRef,
        usage: RawUsage,
        schema: type[BaseModel] | None = None,
    ) -> tuple[RawUsage, Decimal | None]:
        if not self._dry:
            return usage, None
        return self._estimator.chat(request, ref, schema)

    def _key(self, kind: str, material: Mapping[str, Any]) -> str | None:
        if self._cache is None:
            return None
        key = self._cache.key(self.name, kind, material)
        dump = os.environ.get("LIVE_DUMP_REQUESTS")
        if dump:  # to see why two identical-looking runs missed the cache: diff two dumps
            path = Path(dump)
            path.mkdir(parents=True, exist_ok=True)
            step = str(material.get("step", kind))
            (path / f"{step}-{key[:10]}.json").write_text(
                json.dumps(material, indent=1, sort_keys=True, default=str), encoding="utf-8"
            )
        return key

    def _store(self, key: str | None, value: dict[str, Any]) -> None:
        if self._cache is not None and key is not None:
            self._cache.put(key, value)

    def _lookup(self, key: str | None) -> dict[str, Any] | None:
        return None if self._cache is None or key is None else self._cache.get(key)

    async def stream_chat(self, request: AdapterRequest) -> AsyncIterator[AdapterStreamEvent]:
        started = time.perf_counter()
        ref = self._ref(request.step, request.model)
        key = self._key("chat", request_material(request))
        cached = self._lookup(key)
        if cached is not None:
            reply = _reply_of(cached)
            self._record(
                request.step, ref, reply.usage, started, ttft=started, hit=True, output=reply.text
            )
            if reply.text:
                yield TextDelta(reply.text)
            yield StreamEnd(reply)
            return
        ttft: float | None = None
        final: AdapterReply | None = None
        try:
            async for event in self._inner.stream_chat(request):
                if isinstance(event, TextDelta) and ttft is None:
                    ttft = time.perf_counter()
                if isinstance(event, StreamEnd):
                    final = event.reply
                yield event
        except ProviderError as err:
            self._failed(request, err, started)
            raise
        if final is not None:
            usage, cost = self._cost(request, ref, final.usage)
            self._record(request.step, ref, usage, started, ttft=ttft, cost=cost, output=final.text)
            self._store(key, _reply_json(final, request, ref))

    async def chat(self, request: AdapterRequest) -> AdapterReply:
        started = time.perf_counter()
        ref = self._ref(request.step, request.model)
        key = self._key("chat", request_material(request))
        cached = self._lookup(key)
        if cached is not None:
            reply = _reply_of(cached)
            self._record(request.step, ref, reply.usage, started, hit=True, output=reply.text)
            return reply
        try:
            reply = await self._inner.chat(request)
        except ProviderError as err:
            self._failed(request, err, started)
            raise
        usage, cost = self._cost(request, ref, reply.usage)
        self._record(request.step, ref, usage, started, cost=cost, output=reply.text)
        self._store(key, _reply_json(reply, request, ref))
        return reply

    async def structured[T: BaseModel](
        self, request: AdapterRequest, schema: type[T]
    ) -> AdapterStructured[T]:
        started = time.perf_counter()
        ref = self._ref(request.step, request.model)
        material = {
            **request_material(request),
            "schema": schema.__name__,
            "schema_json": schema.model_json_schema(),
        }
        key = self._key("structured", material)
        cached = self._lookup(key)
        if cached is not None:
            value = schema.model_validate(cached["value"])
            usage = _usage_of(cached["usage"])
            self._record(request.step, ref, usage, started, hit=True, output=cached["value"])
            return AdapterStructured(value=value, usage=usage)
        try:
            result = await self._inner.structured(request, schema)
        except ProviderError as err:
            self._failed(request, err, started, schema)
            raise
        usage, cost = self._cost(request, ref, result.usage, schema)
        dumped = result.value.model_dump(mode="json")
        self._record(request.step, ref, usage, started, cost=cost, output=dumped)
        self._store(
            key,
            {
                "meta": _meta(request, ref),
                "value": result.value.model_dump(mode="json"),
                "usage": _usage_json(result.usage),
            },
        )
        return result

    async def embed(
        self, model: str, texts: Sequence[str], dimensions: int | None = None
    ) -> AdapterEmbedding:
        started = time.perf_counter()
        ref = self._ref("embed", model)
        key = self._key("embed", {"model": model, "texts": list(texts), "dimensions": dimensions})
        cached = self._lookup(key)
        if cached is not None:
            usage = _usage_of(cached["usage"])
            self._record("embed", ref, usage, started, hit=True)
            return AdapterEmbedding(vectors=cached["vectors"], usage=usage)
        try:
            result = await self._inner.embed(model, texts, dimensions)
        except ProviderError as err:
            self._record(
                "embed",
                ref,
                RawUsage(input_tokens=0, output_tokens=0),
                started,
                cost=Decimal(0),
                failed=err.kind.value,
            )
            raise
        usage, cost = self._estimator.embed(texts, ref) if self._dry else (result.usage, None)
        self._record("embed", ref, usage, started, cost=cost)
        self._store(
            key,
            {
                "meta": {"step": "embed", "ref": str(ref)},
                "vectors": result.vectors,
                "usage": _usage_json(result.usage),
            },
        )
        return result

    async def aclose(self) -> None:
        await self._inner.aclose()


def _meta(request: AdapterRequest, ref: ModelRef) -> dict[str, str]:
    return {"step": request.step, "ref": str(ref)}


def _reply_json(reply: AdapterReply, request: AdapterRequest, ref: ModelRef) -> dict[str, Any]:
    return {
        "meta": _meta(request, ref),
        "text": reply.text,
        "tool_calls": [c.model_dump(mode="json") for c in reply.tool_calls],
        "stop_reason": reply.stop_reason,
        "usage": _usage_json(reply.usage),
    }


def _reply_of(cached: Mapping[str, Any]) -> AdapterReply:
    return AdapterReply(
        text=str(cached.get("text", "")),
        tool_calls=[ToolCall.model_validate(c) for c in cached.get("tool_calls", [])],
        stop_reason=cached.get("stop_reason"),
        usage=_usage_of(cached.get("usage", {})),
    )


def _loaded(raw: str | None) -> Any:
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return raw
