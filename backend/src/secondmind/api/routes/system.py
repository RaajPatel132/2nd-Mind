"""Liveness, readiness and build/config metadata."""

from collections.abc import Callable
from decimal import Decimal

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from secondmind.api.deps import ServicesDep
from secondmind.api.schemas import (
    CheckOut,
    HealthOut,
    MetaOut,
    ModelChoiceOut,
    PickerOut,
    ReadyOut,
    RouteOut,
)
from secondmind.config import AppConfig, ModelRef, Step

router = APIRouter()


@router.get("/healthz", response_model=HealthOut, tags=["system"])
async def healthz() -> HealthOut:
    """Process is alive (no dependencies checked)."""
    return HealthOut(status="ok")


@router.get(
    "/readyz",
    response_model=ReadyOut,
    responses={503: {"model": ReadyOut, "description": "A dependency is not ready"}},
    tags=["system"],
)
async def readyz(services: ServicesDep) -> JSONResponse:
    """Database (schema at head, app role under RLS), Redis, and provider config."""
    results = await services.run_checks()
    ready = all(r.ok for r in results.values())
    body = ReadyOut(
        status="ready" if ready else "not_ready",
        checks={n: CheckOut(ok=r.ok, detail=r.detail) for n, r in results.items()},
    )
    return JSONResponse(status_code=200 if ready else 503, content=body.model_dump(mode="json"))


@router.get("/v1/meta", response_model=MetaOut, tags=["system"])
async def meta(services: ServicesDep) -> MetaOut:
    """Build, routing and config hash this process runs with (FR-19.2)."""
    config = services.config
    settings = config.settings
    return MetaOut(
        name="2nd Mind",
        version=settings.app_version,
        env=settings.env,
        config_hash=config.config_hash,
        config_hash_short=config.config_hash_short,
        provider_mode=config.routing.mode,
        price_version=config.prices.version,
        picker=picker_out(config),
        routes=[
            RouteOut(
                step=r.step.value,
                provider=r.primary.provider,
                model=r.primary.model,
                fallback=str(r.fallback) if r.fallback else None,
                prompt=r.prompt,
                timeout_s=r.timeout_s,
            )
            for r in config.routing.routes.values()
        ],
        prompts=config.prompts.refs(),
        substitutions=config.routing.substitutions,
        dev_auth=settings.dev_auth,
        access_code_required=settings.dev_auth and settings.env == "staging",
        tracing_enabled=services.tracer.enabled,
    )


def picker_out(config: AppConfig) -> PickerOut | None:
    routing, prices = config.routing, config.prices
    if not routing.choices or not routing.typical_turn:
        return None

    def turn_cost(model_for: Callable[[Step], ModelRef]) -> Decimal:
        return sum(
            (
                prices.cost(
                    model_for(step),
                    input_tokens=profile.input,
                    cached_input_tokens=0,
                    output_tokens=profile.output,
                )
                for step, profile in routing.typical_turn.items()
            ),
            Decimal(0),
        )

    def auto_model(step: Step) -> ModelRef:
        route = routing.route(step)
        return route.configured or route.primary

    auto = turn_cost(auto_model)

    def relative(ref: ModelRef) -> float:
        return round(float(turn_cost(lambda _step: ref) / auto), 1) if auto else 1.0

    return PickerOut(
        auto_note="Each step runs on the model that suits it: the best answer at the lowest price.",
        auto_usd_per_turn=float(auto),
        choices=[
            ModelChoiceOut(
                id=str(c.ref),
                label=c.label,
                provider=c.ref.provider,
                provider_label=c.provider_label,
                relative_price=relative(c.ref),
                tiers=list(c.tiers),
                simulated=c.simulated,
                available=c.available,
            )
            for c in routing.choices
        ],
    )
