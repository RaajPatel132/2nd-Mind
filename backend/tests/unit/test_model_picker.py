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
from secondmind.core import ConfigError

OPUS = ModelRef(provider="anthropic", model="claude-opus-5")
SONNET = ModelRef(provider="anthropic", model="claude-sonnet-5")
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


def test_without_keys_auto_mode_simulates_every_choice(base_env: dict[str, str]) -> None:
    routing = load_app_config(base_env).routing
    assert routing.default_choice == SONNET
    assert [c.provider_label for c in routing.choices][:2] == ["Anthropic", "Anthropic"]
    for choice in routing.choices:
        assert choice.available
        assert choice.simulated
        assert choice.served_by == ModelRef(provider="fake", model=choice.ref.model)


def test_a_key_serves_its_provider_choices_for_real(base_env: dict[str, str]) -> None:
    routing = load_app_config(base_env | {"ANTHROPIC_API_KEY": "k"}).routing
    opus, sol = routing.choice(OPUS), routing.choice(SOL)
    assert opus is not None
    assert sol is not None
    assert opus.served_by == OPUS
    assert not opus.simulated
    assert sol.simulated


def test_fake_mode_simulates_even_with_keys(base_env: dict[str, str]) -> None:
    routing = load_app_config(base_env | BOTH_KEYS | {"MODEL_PROVIDER_MODE": "fake"}).routing
    assert all(c.simulated and c.available for c in routing.choices)


def test_live_mode_marks_a_keyless_choice_unavailable(base_env: dict[str, str]) -> None:
    file = read_routing_file(_config_file(base_env, "models.yaml"))
    routing = resolve_routing(file, BOTH_KEYS, "live")
    assert all(c.available and not c.simulated for c in routing.choices)
    # Drop OpenAI's key without tripping the route checks: resolve the picker alone.
    only_anthropic = file.model_copy(
        update={
            "steps": {s: c.model_copy(update={"fallback": None}) for s, c in file.steps.items()}
        }
    )
    routing = resolve_routing(
        only_anthropic, {"ANTHROPIC_API_KEY": "k", "MODEL_EMBED": "anthropic:x"}, "live"
    )
    sol = routing.choice(SOL)
    assert sol is not None
    assert not sol.available
    with pytest.raises(ValueError, match="no credentials"):
        routing.with_pick(SOL)


def test_a_pick_routes_every_chat_step_and_keeps_embeddings(base_env: dict[str, str]) -> None:
    routing = load_app_config(base_env | BOTH_KEYS).routing
    picked = routing.with_pick(OPUS)
    for step, route in picked.routes.items():
        if route.kind is StepKind.EMBEDDING:
            assert route == routing.route(step)
        else:
            assert route.primary == OPUS
            assert route.fallback == routing.route(step).fallback  # each step keeps its own
    # Picking the fallback's own model drops the fallback rather than retrying itself.
    assert picked.with_pick(SOL).route(Step.ANSWER).fallback is None


def test_a_simulated_pick_has_no_fallback(base_env: dict[str, str]) -> None:
    routing = load_app_config(base_env).routing.with_pick(OPUS)
    answer = routing.route(Step.ANSWER)
    assert answer.primary == ModelRef(provider="fake", model="claude-opus-5")
    assert answer.fallback is None


def test_picking_an_unknown_model_fails(base_env: dict[str, str]) -> None:
    routing = load_app_config(base_env).routing
    with pytest.raises(ValueError, match="not a model you can pick"):
        routing.with_pick(ModelRef(provider="anthropic", model="claude-2"))


@pytest.mark.parametrize(
    ("old", "new", "error"),
    [
        ("default: anthropic:claude-sonnet-5", "default: openai:gpt-4o", "not one of"),
        ("    openai:gpt-6-luna: GPT-6 Luna", "    mystery:m: M", "unknown provider"),
        ("    openai:gpt-6-luna: GPT-6 Luna", "    fake:fake-chat: Fake", "can't be picked"),
    ],
)
def test_a_bad_picker_fails_with_what_is_wrong(
    base_env: dict[str, str], tmp_path: Path, old: str, new: str, error: str
) -> None:
    text = _config_file(base_env, "models.yaml").read_text()
    assert old in text
    path = tmp_path / "models.yaml"
    path.write_text(text.replace(old, new))
    with pytest.raises(ConfigError, match=error):
        resolve_routing(read_routing_file(path), {}, "auto")


def test_every_picker_model_needs_a_price(base_env: dict[str, str], tmp_path: Path) -> None:
    config = load_app_config(base_env)
    assert all(config.prices.weight(c.ref) > 0 for c in config.routing.choices)
