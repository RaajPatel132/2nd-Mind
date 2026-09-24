"""Model steps for one turn: every call goes through the router with a step name and a versioned
prompt, is traced, and is recorded (``model_call`` event + usage-ledger row). Nothing here knows
which provider served it."""

import json
import math
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import replace
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel

from secondmind.config import PromptRegistry, Step
from secondmind.core import Usage
from secondmind.observability import GenerationSpan, TurnTrace
from secondmind.policy import redact_values
from secondmind.providers import ChatMessage, ModelCall, ModelRouter, ProviderUnavailableError

RecordModelCall = Callable[[ModelCall, GenerationSpan, object, str], Awaitable[None]]


class ModelSteps:
    def __init__(
        self,
        *,
        router: ModelRouter,
        prompts: PromptRegistry,
        trace: TurnTrace,
        record: RecordModelCall,
        now: datetime,
        cache_prefix: str | None = None,
        embed_dimensions: int = 1536,
    ) -> None:
        self._router = router
        self._prompts = prompts
        self._trace = trace
        self._record = record
        self._now = now
        self.cache_prefix = cache_prefix
        self._dims = embed_dimensions
        # Secret values a model labelled; scrubbed from anything sent to the trace from now on.
        self.secrets: list[str] = []

    def model_of(self, step: Step) -> str:
        route = self._router.route(step)
        return f"{route.primary.provider}:{route.primary.model}"

    @property
    def embedding_model(self) -> str:
        """The key under which embeddings are cached: model and size."""
        return f"{self.model_of(Step.EMBED)}@{self._dims}"

    async def structured[T: BaseModel](
        self,
        step: Step,
        schema: type[T],
        variables: BaseModel,
        messages: Sequence[ChatMessage],
    ) -> T:
        route = self._router.route(step)
        if route.prompt is None:
            raise RuntimeError(f"the {step.value} step has no prompt configured")
        system = self._prompts.render(route.prompt, variables).text
        span = self._trace.start_generation(step.value)
        try:
            result = await self._router.structured(
                step,
                schema,
                system=system,
                messages=list(messages),
                prompt=route.prompt,
                cache_prefix=self.cache_prefix,
            )
        except ProviderUnavailableError as exc:
            span.fail(exc.detail)
            raise
        self.secrets.extend(_labelled_secrets(result.value))
        prompt_input: object = [m.model_dump() for m in messages]
        output = result.value.model_dump_json()
        if self.secrets:
            prompt_input = redact_values(json.dumps(prompt_input), self.secrets)
            output = redact_values(output, self.secrets)
        await self._record(result.call, span, prompt_input, output)
        return result.value

    async def embed(self, texts: Sequence[str], cache_hits: int = 0) -> list[list[float]] | None:
        """Embed ``texts``; ``cache_hits`` (reused by content hash) are shown on the event."""
        span = self._trace.start_generation(Step.EMBED.value)
        if not texts:
            await self._record(self._cached_call(cache_hits), span, None, "")
            return []
        try:
            result = await self._router.embed(list(texts), dimensions=self._dims)
        except ProviderUnavailableError as exc:
            span.fail(exc.detail)
            raise
        call = replace(result.call, cache_hits=cache_hits)
        await self._record(call, span, None, f"{len(result.vectors)} vectors")
        return result.vectors

    async def similarity(self, text: str, candidates: Sequence[str]) -> tuple[int, float] | None:
        """Best candidate for ``text`` by cosine similarity of embeddings."""
        if not candidates:
            return None
        vectors = await self.embed([text, *candidates])
        if not vectors:
            return None
        query = vectors[0]
        scores = [cosine(query, v) for v in vectors[1:]]
        best = max(range(len(scores)), key=scores.__getitem__)
        return best, scores[best]

    async def embed_one(self, text: str) -> list[float] | None:
        vectors = await self.embed([text])
        return vectors[0] if vectors else None

    def _cached_call(self, hits: int) -> ModelCall:
        route = self._router.route(Step.EMBED)
        usage = Usage(
            provider=route.primary.provider,
            model=route.primary.model,
            input_tokens=0,
            output_tokens=0,
            cost_usd=Decimal(0),
            latency_ms=0,
            price_version="cache",
        )
        return ModelCall(
            step=Step.EMBED.value,
            provider=route.primary.provider,
            model=route.primary.model,
            prompt=None,
            started_at=self._now,
            latency_ms=0,
            time_to_first_token_ms=None,
            attempts=0,
            usage=usage,
            fallback=None,
            cache_hits=hits,
        )


def _labelled_secrets(value: BaseModel) -> list[str]:
    """Secret spans an extraction reported (``secret_spans``), if the output has any."""
    spans = getattr(value, "secret_spans", None)
    return [s for s in spans if isinstance(s, str) and s.strip()] if isinstance(spans, list) else []


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (na * nb)
