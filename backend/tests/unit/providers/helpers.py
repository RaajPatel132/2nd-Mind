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

# The prompt each step renders (as in config/models.yaml).
PROMPTS_BY_STEP: dict[Step, str] = {
    Step.INTENT: "intent@1",
    Step.EXTRACT: "extract@1",
    Step.ENRICH: "enrich@1",
    Step.RECONCILE: "reconcile@1",
    Step.RESOLVE: "resolve@1",
    Step.PLAN: "plan@1",
    Step.RERANK: "rerank@1",
    Step.CORRECT: "correct@1",
    Step.ANSWER: "answer@3",
}


def routing(
    primary: str,
    fallback: str | None = None,
    *,
    timeout_s: float = 5.0,
    step: Step = Step.ANSWER,
    prompt: str | None = None,
    all_steps: bool = False,
) -> Routing:
    """Routes for one step, or (``all_steps``) every step: chat steps on ``primary`` with
    their prompts, and embeddings on ``<primary provider>:e``."""
    p = ModelRef.parse(primary)
    f = ModelRef.parse(fallback) if fallback else None
    names = {p.provider} | ({f.provider} if f else set())
    steps = list(Step) if all_steps else [step]
    routes: dict[Step, ResolvedRoute] = {}
    for s in steps:
        kind = StepKind.EMBEDDING if s is Step.EMBED else StepKind.CHAT
        routes[s] = ResolvedRoute(
            step=s,
            kind=kind,
            primary=ModelRef(provider=p.provider, model="e")
            if kind is StepKind.EMBEDDING and all_steps
            else p,
            fallback=None if kind is StepKind.EMBEDDING and all_steps else f,
            timeout_s=timeout_s,
            max_output_tokens=256,
            effort=None,
            prompt=(PROMPTS_BY_STEP.get(s) if all_steps else prompt),
        )
    return Routing(
        mode="auto",
        providers={
            n: ResolvedProvider(name=n, kind="fake", has_credentials=True) for n in sorted(names)
        },
        routes=routes,
    )


def prices(*refs: str) -> PriceTable:
    models: dict[str, dict[str, ModelPrice]] = {}
    for raw in refs:
        ref = ModelRef.parse(raw)
        models.setdefault(ref.provider, {})[ref.model] = PRICE
    # Every model has the same price, so each weighs 1 and the quota counts raw tokens.
    return PriceTable(version="test-prices", baseline=refs[0], models=models)


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
    all_steps: bool = False,
) -> ModelRouter:
    refs = [primary] + ([fallback] if fallback else [])
    if all_steps:
        refs.append(f"{ModelRef.parse(primary).provider}:e")
    return ModelRouter(
        routing=routing(
            primary, fallback, timeout_s=timeout_s, step=step, prompt=prompt, all_steps=all_steps
        ),
        prices=prices(*refs),
        adapters=adapters,
        policy=policy or ResiliencePolicy(max_retries=2),
        sleep=sleep or Sleeps(),
        monotonic=clock or Clock(),
        rand=lambda: 0.5,
    )


def fakes(*names: str) -> dict[str, FakeProvider]:
    return {n: FakeProvider(n) for n in names}
