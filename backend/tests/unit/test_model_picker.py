"""The model picker and weighted quota (ADR-0030): choices per provider mode, a pick's routes,
and each model's weight against the baseline."""

from decimal import Decimal
from pathlib import Path

import pytest

from secondmind.config import (
    ModelRef,
    Step,
    StepKind,
    load_app_config,
    read_price_table,
    read_routing_file,
    resolve_routing,
)
from secondmind.core import ConfigError, Tier

OPUS = ModelRef(provider="anthropic", model="claude-opus-5")
SONNET = ModelRef(provider="anthropic", model="claude-sonnet-5")
HAIKU = ModelRef(provider="anthropic", model="claude-haiku-4-5")
LUNA = ModelRef(provider="openai", model="gpt-6-luna")
MINI = ModelRef(provider="openai", model="gpt-5.4-mini")
SOL = ModelRef(provider="openai", model="gpt-6-sol")
BOTH_KEYS = {"ANTHROPIC_API_KEY": "k", "OPENAI_API_KEY": "k"}


def _config_file(base_env: dict[str, str], name: str) -> Path:
    return load_app_config(base_env).settings.resources_dir / "config" / name


def test_weights_are_blended_price_over_the_baseline(base_env: dict[str, str]) -> None:
    prices = load_app_config(base_env).prices
    assert prices.baseline_ref == SONNET
    assert prices.weight(SONNET) == Decimal(1)
    # (3 * 5 + 25) / (3 * 2 + 10) = 40 / 16
    assert prices.weight(OPUS) == Decimal("2.5")
    assert prices.weight(ModelRef(provider="anthropic", model="claude-fable-5-1")) == Decimal(5)
    assert prices.weight(ModelRef(provider="anthropic", model="claude-haiku-4-5")) == Decimal("0.5")
    # (3 * 0.75 + 4.5) / 16 = 0.421875
    assert prices.weight(ModelRef(provider="openai", model="gpt-5.4-mini")) == Decimal("0.4219")


def test_charged_tokens_round_half_up(base_env: dict[str, str]) -> None:
    prices = load_app_config(base_env).prices
    assert prices.charged_tokens(OPUS, 1_000) == 2_500
    assert prices.charged_tokens(SONNET, 0) == 0
    # 3 * 0.5 = 1.5 -> 2
    assert prices.charged_tokens(ModelRef(provider="anthropic", model="claude-haiku-4-5"), 3) == 2


def test_the_fake_provider_standing_in_is_priced_as_the_model(base_env: dict[str, str]) -> None:
    prices = load_app_config(base_env).prices
    stand_in = ModelRef(provider="fake", model="claude-opus-5")
    assert prices.price_for(stand_in) == prices.price_for(OPUS)
    assert prices.weight(stand_in) == Decimal("2.5")
    with pytest.raises(ConfigError, match="no price"):
        prices.price_for(ModelRef(provider="fake", model="nothing-like-it"))


def test_a_baseline_without_a_price_fails_at_start(
    base_env: dict[str, str], tmp_path: Path
) -> None:
    text = _config_file(base_env, "prices.yaml").read_text()
    path = tmp_path / "prices.yaml"
    path.write_text(text.replace("baseline: anthropic:claude-sonnet-5", "baseline: x:nope"))
    with pytest.raises(ConfigError, match="baseline x:nope has no price"):
        read_price_table(path)


def test_auto_is_the_default_and_the_picker_offers_the_affordable_models(
    base_env: dict[str, str],
) -> None:
    routing = load_app_config(base_env).routing
    assert [str(c.ref) for c in routing.choices] == [
        "openai:gpt-6-luna",
        "openai:gpt-5.4-mini",
        "anthropic:claude-haiku-4-5",
        "anthropic:claude-sonnet-5",
    ]
    for gone in ("claude-fable-5-1", "claude-opus-5-5", "claude-opus-5"):
        assert routing.choice(ModelRef(provider="anthropic", model=gone)) is None
    assert routing.choice(SOL) is None  # still priced, so old turns cost right; not offered
    assert load_app_config(base_env).prices.price_for(SOL)


def test_each_tier_gets_what_its_budget_allows(base_env: dict[str, str]) -> None:
    routing = load_app_config(base_env).routing

    def offered(tier: Tier) -> set[str]:
        return {str(c.ref) for c in routing.choices if tier in c.tiers}

    assert offered(Tier.GUEST) == set()  # Auto only
    assert offered(Tier.STANDARD) == {str(LUNA), str(MINI), str(HAIKU)}
    assert offered(Tier.PREMIUM) == offered(Tier.STANDARD) | {str(SONNET)}


def test_without_keys_auto_mode_simulates_every_choice(base_env: dict[str, str]) -> None:
    routing = load_app_config(base_env).routing
    for choice in routing.choices:
        assert choice.available
        assert choice.simulated
        assert choice.served_by == ModelRef(provider="fake", model=choice.ref.model)
    # And Auto's own steps are all on the fake provider, quoted as the models they stand in for.
    rerank = routing.route(Step.RERANK)
    assert rerank.primary.provider == "fake"
    assert rerank.configured == MINI


def test_a_key_serves_its_provider_choices_for_real(base_env: dict[str, str]) -> None:
    routing = load_app_config(base_env | {"ANTHROPIC_API_KEY": "k"}).routing
    haiku, luna = routing.choice(HAIKU), routing.choice(LUNA)
    assert haiku is not None
    assert luna is not None
    assert haiku.served_by == HAIKU
    assert not haiku.simulated
    assert luna.simulated


def test_fake_mode_simulates_even_with_keys(base_env: dict[str, str]) -> None:
    routing = load_app_config(base_env | BOTH_KEYS | {"MODEL_PROVIDER_MODE": "fake"}).routing
    assert all(c.simulated and c.available for c in routing.choices)


def test_live_mode_marks_a_keyless_choice_unavailable(base_env: dict[str, str]) -> None:
    file = read_routing_file(_config_file(base_env, "models.yaml"))
    routing = resolve_routing(file, BOTH_KEYS, "live")
    assert all(c.available and not c.simulated for c in routing.choices)
    # Drop OpenAI's key without tripping the route checks: resolve the picker alone.
    on_anthropic = {"provider": "anthropic", "model": "claude-haiku-4-5", "fallback": None}
    only_anthropic = file.model_copy(
        update={"steps": {s: c.model_copy(update=on_anthropic) for s, c in file.steps.items()}}
    )
    routing = resolve_routing(
        only_anthropic, {"ANTHROPIC_API_KEY": "k", "MODEL_EMBED": "anthropic:x"}, "live"
    )
    luna = routing.choice(LUNA)
    assert luna is not None
    assert not luna.available
    with pytest.raises(ValueError, match="no credentials"):
        routing.with_pick(LUNA)


def test_a_pick_routes_every_chat_step_and_keeps_embeddings(base_env: dict[str, str]) -> None:
    routing = load_app_config(base_env | BOTH_KEYS).routing
    picked = routing.with_pick(SONNET)
    for step, route in picked.routes.items():
        if route.kind is StepKind.EMBEDDING:
            assert route == routing.route(step)
        else:
            assert route.primary == SONNET
            assert route.fallback == routing.route(step).fallback  # each step keeps its own
    # Picking the fallback's own model drops the fallback rather than retrying itself.
    answer_fallback = routing.route(Step.ANSWER).fallback
    assert answer_fallback == MINI
    assert picked.with_pick(answer_fallback).route(Step.ANSWER).fallback is None


def test_a_simulated_pick_has_no_fallback(base_env: dict[str, str]) -> None:
    routing = load_app_config(base_env).routing.with_pick(SONNET)
    answer = routing.route(Step.ANSWER)
    assert answer.primary == ModelRef(provider="fake", model="claude-sonnet-5")
    assert answer.fallback is None


def test_picking_an_unknown_model_fails(base_env: dict[str, str]) -> None:
    routing = load_app_config(base_env).routing
    with pytest.raises(ValueError, match="not a model you can pick"):
        routing.with_pick(ModelRef(provider="anthropic", model="claude-2"))


def _picker_text(base_env: dict[str, str]) -> tuple[str, str]:
    """models.yaml split into its picker section and everything after it."""
    text = _config_file(base_env, "models.yaml").read_text()
    head, sep, tail = text.partition("\nsteps:")
    return head, sep + tail


@pytest.mark.parametrize(
    ("swap", "error"),
    [
        (
            (
                "standard: [openai:gpt-6-luna, openai:gpt-5.4-mini, anthropic:claude-haiku-4-5]",
                "standard: [openai:gpt-4o]",
            ),
            "not a picker model",
        ),
        (("    guest: []\n", ""), "needs an entry for: guest"),
        (("openai:gpt-6-luna", "mystery:m"), "unknown provider"),
        (("openai:gpt-6-luna", "fake:fake-chat"), "can't be picked"),
    ],
)
def test_a_bad_picker_fails_with_what_is_wrong(
    base_env: dict[str, str], tmp_path: Path, swap: tuple[str, str], error: str
) -> None:
    head, tail = _picker_text(base_env)
    old, new = swap
    assert old in head
    path = tmp_path / "models.yaml"
    path.write_text(head.replace(old, new) + tail)
    with pytest.raises(ConfigError, match=error):
        resolve_routing(read_routing_file(path), {}, "auto")


def test_every_picker_model_needs_a_price(base_env: dict[str, str], tmp_path: Path) -> None:
    config = load_app_config(base_env)
    assert all(config.prices.weight(c.ref) > 0 for c in config.routing.choices)
