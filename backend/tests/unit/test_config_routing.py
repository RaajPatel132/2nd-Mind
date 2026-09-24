"""S1.4: per-step routing, env overrides, provider modes, prices and the config hash."""

from decimal import Decimal
from pathlib import Path

import pytest

from secondmind.config import (
    ModelRef,
    Step,
    load_app_config,
    read_routing_file,
    resolve_routing,
)
from secondmind.core import ConfigError


def test_default_routing_without_keys_uses_fake_in_auto_mode(base_env: dict[str, str]) -> None:
    config = load_app_config(base_env)
    answer = config.routing.route(Step.ANSWER)
    assert answer.primary == ModelRef(provider="fake", model="fake-chat")
    assert answer.fallback is None
    assert config.routing.route(Step.EMBED).primary == ModelRef(provider="fake", model="fake-embed")
    assert any("has no credentials" in s for s in config.routing.substitutions)


def test_every_step_has_provider_model_and_timeout(base_env: dict[str, str]) -> None:
    config = load_app_config(base_env | {"ANTHROPIC_API_KEY": "k", "OPENAI_API_KEY": "k"})
    assert set(config.routing.routes) == set(Step)
    for route in config.routing.routes.values():
        assert route.primary.provider
        assert route.primary.model
        assert route.timeout_s > 0


def test_keys_present_route_to_configured_providers(base_env: dict[str, str]) -> None:
    config = load_app_config(base_env | {"ANTHROPIC_API_KEY": "k", "OPENAI_API_KEY": "k"})
    answer = config.routing.route(Step.ANSWER)
    assert answer.primary.provider == "anthropic"
    assert answer.fallback is not None
    assert answer.fallback.provider == "openai"
    assert config.routing.substitutions == []


def test_only_fallback_key_promotes_fallback(base_env: dict[str, str]) -> None:
    config = load_app_config(base_env | {"OPENAI_API_KEY": "k"})
    answer = config.routing.route(Step.ANSWER)
    assert answer.primary.provider == "openai"
    assert answer.fallback is None


def test_env_override_switches_a_step_by_config_only(base_env: dict[str, str]) -> None:
    env = base_env | {"ANTHROPIC_API_KEY": "k", "OPENAI_API_KEY": "k"}
    before = load_app_config(env)
    after = load_app_config(
        env | {"MODEL_ANSWER": "openai:gpt-6-sol", "MODEL_ANSWER_FALLBACK": "none"}
    )
    assert after.routing.route(Step.ANSWER).primary == ModelRef(
        provider="openai", model="gpt-6-sol"
    )
    assert after.routing.route(Step.ANSWER).fallback is None
    assert after.config_hash != before.config_hash


@pytest.mark.parametrize(
    ("key", "value", "expected"),
    [
        ("MODEL_ANSWR", "openai:gpt-6-sol", "MODEL_ANSWR: unknown step"),
        ("MODEL_ANSWER", "gpt-6-sol", "expected '<provider>:<model>'"),
        ("MODEL_ANSWER", "mystery:model", "unknown provider 'mystery'"),
    ],
)
def test_bad_overrides_fail_with_the_variable_name(
    base_env: dict[str, str], key: str, value: str, expected: str
) -> None:
    with pytest.raises(ConfigError, match=expected):
        load_app_config(base_env | {key: value})


def test_routed_model_without_price_fails(base_env: dict[str, str]) -> None:
    env = base_env | {"OPENAI_API_KEY": "k", "MODEL_ANSWER": "openai:gpt-unpriced"}
    with pytest.raises(ConfigError, match=r"no price for routed model.*openai:gpt-unpriced"):
        load_app_config(env)


def test_live_mode_requires_credentials(base_env: dict[str, str]) -> None:
    with pytest.raises(ConfigError, match="ANTHROPIC_API_KEY") as exc:
        load_app_config(base_env | {"MODEL_PROVIDER_MODE": "live"})
    assert "OPENAI_API_KEY" in exc.value.message


def test_fake_mode_routes_everything_to_fake(base_env: dict[str, str]) -> None:
    env = base_env | {"MODEL_PROVIDER_MODE": "fake", "ANTHROPIC_API_KEY": "k"}
    config = load_app_config(env)
    assert {r.primary.provider for r in config.routing.routes.values()} == {"fake"}


def test_openai_compatible_provider_registers_via_config_only(
    base_env: dict[str, str], tmp_path: Path
) -> None:
    config = load_app_config(base_env)
    src = config.settings.resources_dir / "config" / "models.yaml"
    text = src.read_text().replace(
        "  fake:\n    kind: fake",
        "  vllm:\n    kind: openai\n    base_url_env: VLLM_BASE_URL\n  fake:\n    kind: fake",
    )
    path = tmp_path / "models.yaml"
    path.write_text(text)
    routing = resolve_routing(
        read_routing_file(path),
        {"VLLM_BASE_URL": "http://vllm:8000/v1", "MODEL_EXTRACT": "vllm:qwen-small"},
        "auto",
    )
    extract = routing.route(Step.EXTRACT)
    assert extract.primary == ModelRef(provider="vllm", model="qwen-small")
    assert routing.providers["vllm"].base_url == "http://vllm:8000/v1"


def test_config_hash_is_stable_and_exposed_short(base_env: dict[str, str]) -> None:
    a = load_app_config(base_env)
    b = load_app_config(base_env)
    assert a.config_hash == b.config_hash
    assert len(a.config_hash) == 64
    assert a.config_hash_short == a.config_hash[:12]


def test_config_hash_ignores_secret_values(base_env: dict[str, str]) -> None:
    a = load_app_config(base_env | {"ANTHROPIC_API_KEY": "one", "OPENAI_API_KEY": "x"})
    b = load_app_config(base_env | {"ANTHROPIC_API_KEY": "two", "OPENAI_API_KEY": "y"})
    assert a.config_hash == b.config_hash


def test_price_table_costs_per_million(base_env: dict[str, str]) -> None:
    prices = load_app_config(base_env).prices
    cost = prices.cost(
        ModelRef(provider="anthropic", model="claude-opus-5"),
        input_tokens=1_000,
        cached_input_tokens=2_000,
        output_tokens=500,
    )
    # 1000*5 + 2000*0.5 + 500*25 = 18500 per 1M -> 0.0185
    assert cost == Decimal("0.0185")
    assert prices.version
