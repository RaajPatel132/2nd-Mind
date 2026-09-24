"""Provider adapters (SDK code lives only here) and the factory that builds the router."""

import os
from collections.abc import Mapping

from secondmind.config import AppConfig, ResolvedProvider
from secondmind.providers import (
    FakeProvider,
    FakeScript,
    ModelRouter,
    ProviderAdapter,
    ResiliencePolicy,
)
from secondmind.providers.adapters.anthropic_adapter import AnthropicAdapter
from secondmind.providers.adapters.openai_adapter import OpenAIAdapter


def build_adapter(
    provider: ResolvedProvider,
    environ: Mapping[str, str],
    *,
    fake_token_delay_s: float = 0.0,
    fake_script: FakeScript | None = None,
) -> ProviderAdapter:
    key = environ.get(provider.api_key_env, "").strip() if provider.api_key_env else ""
    match provider.kind:
        case "anthropic":
            return AnthropicAdapter(provider.name, api_key=key, base_url=provider.base_url)
        case "openai":
            return OpenAIAdapter(provider.name, api_key=key or None, base_url=provider.base_url)
        case "fake":
            return FakeProvider(provider.name, script=fake_script, token_delay_s=fake_token_delay_s)


def build_router(
    config: AppConfig,
    environ: Mapping[str, str] | None = None,
    *,
    fake_script: FakeScript | None = None,
) -> ModelRouter:
    """Build adapters for every provider a route uses, and the router over them."""
    env = os.environ if environ is None else environ
    settings = config.settings
    used = {ref.provider for ref in config.routing.refs()}
    adapters = {
        name: build_adapter(
            config.routing.providers[name],
            env,
            fake_token_delay_s=settings.fake_provider_token_delay_ms / 1000,
            fake_script=fake_script,
        )
        for name in sorted(used)
    }
    policy = ResiliencePolicy(
        max_retries=settings.provider_max_retries,
        base_delay_s=settings.provider_retry_base_ms / 1000,
        max_delay_s=settings.provider_retry_max_ms / 1000,
        breaker_threshold=settings.breaker_failure_threshold,
        breaker_window_s=settings.breaker_window_s,
        breaker_cooldown_s=settings.breaker_cooldown_s,
    )
    return ModelRouter(
        routing=config.routing, prices=config.prices, adapters=adapters, policy=policy
    )


async def close_router_adapters(router: ModelRouter) -> None:
    await router.aclose()


__all__ = [
    "AnthropicAdapter",
    "OpenAIAdapter",
    "build_adapter",
    "build_router",
    "close_router_adapters",
]
