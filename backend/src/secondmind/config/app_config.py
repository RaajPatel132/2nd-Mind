"""Everything the process is configured with, validated together at start-up."""

import hashlib
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from secondmind.config.prices import PriceTable, read_price_table
from secondmind.config.prompts import PromptRegistry
from secondmind.config.routing import Routing, read_routing_file, resolve_routing
from secondmind.config.settings import Settings, load_settings
from secondmind.core import ConfigError


@dataclass(frozen=True, slots=True)
class AppConfig:
    settings: Settings
    routing: Routing
    prices: PriceTable
    prompts: PromptRegistry
    config_hash: str

    @property
    def config_hash_short(self) -> str:
        return self.config_hash[:12]


def load_app_config(environ: Mapping[str, str] | None = None) -> AppConfig:
    """Load and cross-validate settings, routing, prices and prompts.

    Raises :class:`ConfigError` with a message that names what is wrong.
    """
    env = os.environ if environ is None else environ
    settings = load_settings(env)
    config_dir = settings.resources_dir / "config"
    routing = resolve_routing(
        read_routing_file(config_dir / "models.yaml"), env, settings.model_provider_mode
    )
    prices = read_price_table(config_dir / "prices.yaml")
    prices.require(routing.refs() | {c.ref for c in routing.choices})
    prompts = PromptRegistry.load(settings.resources_dir / "prompts")
    for route in routing.routes.values():
        if route.prompt is None:
            continue
        template = prompts.get(route.prompt)
        if template.meta.step != route.step.value:
            raise ConfigError(
                f"step {route.step.value!r} routes to prompt {route.prompt!r}, "
                f"which is written for step {template.meta.step!r}"
            )
    return AppConfig(
        settings=settings,
        routing=routing,
        prices=prices,
        prompts=prompts,
        config_hash=compute_config_hash(routing, prices, prompts),
    )


def compute_config_hash(routing: Routing, prices: PriceTable, prompts: PromptRegistry) -> str:
    """Stable sha256 over resolved routing, prices and prompt versions (FR-19.2).

    Secrets and credential presence are excluded; only what shapes behaviour and cost counts.
    """
    material: dict[str, Any] = {
        "routing": {
            "mode": routing.mode,
            "routes": {
                step.value: route.model_dump(mode="json", exclude={"substituted"})
                for step, route in sorted(routing.routes.items())
            },
            "providers": {
                name: {"kind": p.kind, "base_url": p.base_url}
                for name, p in sorted(routing.providers.items())
            },
            "picker": {
                "default": str(routing.default_choice) if routing.default_choice else None,
                "choices": [c.model_dump(mode="json") for c in routing.choices],
            },
        },
        "prices": prices.model_dump(mode="json"),
        "prompts": prompts.hashes(),
    }
    canonical = json.dumps(material, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
