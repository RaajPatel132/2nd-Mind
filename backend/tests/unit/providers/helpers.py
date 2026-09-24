"""Build small routers over fake providers for resilience and contract tests."""

from decimal import Decimal

from secondmind.config import (
    ModelPrice,
    ModelRef,
    PriceTable,
    ResolvedProvider,
    ResolvedRoute,
    Routing,
    Step,
    StepKind,
)
from secondmind.providers import FakeProvider, ModelRouter, ProviderAdapter, ResiliencePolicy

PRICE = ModelPrice(input=Decimal(1), cached_input=Decimal("0.1"), output=Decimal(5))


def routing(
    primary: str,
    fallback: str | None = None,
    *,
    timeout_s: float = 5.0,
    step: Step = Step.ANSWER,
    prompt: str | None = None,
) -> Routing:
    p = ModelRef.parse(primary)
    f = ModelRef.parse(fallback) if fallback else None
    names = {p.provider} | ({f.provider} if f else set())
    kind = StepKind.EMBEDDING if step is Step.EMBED else StepKind.CHAT
    return Routing(
        mode="auto",
        providers={
            n: ResolvedProvider(name=n, kind="fake", has_credentials=True) for n in sorted(names)
        },
        routes={
            step: ResolvedRoute(
                step=step,
                kind=kind,
                primary=p,
                fallback=f,
                timeout_s=timeout_s,
                max_output_tokens=256,
                effort=None,
                prompt=prompt,
            )
        },
    )


def prices(*refs: str) -> PriceTable:
    models: dict[str, dict[str, ModelPrice]] = {}
    for raw in refs:
        ref = ModelRef.parse(raw)
        models.setdefault(ref.provider, {})[ref.model] = PRICE
    return PriceTable(version="test-prices", models=models)


class Sleeps:
    """Records backoff sleeps instead of waiting."""

    def __init__(self) -> None:
        self.calls: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def router(
    adapters: dict[str, ProviderAdapter],
    primary: str,
    fallback: str | None = None,
    *,
    policy: ResiliencePolicy | None = None,
    sleep: Sleeps | None = None,
    clock: Clock | None = None,
    timeout_s: float = 5.0,
    step: Step = Step.ANSWER,
    prompt: str | None = None,
) -> ModelRouter:
    refs = [primary] + ([fallback] if fallback else [])
    return ModelRouter(
        routing=routing(primary, fallback, timeout_s=timeout_s, step=step, prompt=prompt),
        prices=prices(*refs),
        adapters=adapters,
        policy=policy or ResiliencePolicy(max_retries=2),
        sleep=sleep or Sleeps(),
        monotonic=clock or Clock(),
        rand=lambda: 0.5,
    )


def fakes(*names: str) -> dict[str, FakeProvider]:
    return {n: FakeProvider(n) for n in names}
