"""S1.7 contract tests: one suite over every provider behind the normalised interface.

Always runs against the fake. Runs against Anthropic and OpenAI with ``pytest -m live`` when
their keys are set (skipped in normal CI). Covers streaming, structured output, a tool call
round trip, and usage + cost being filled in.
"""

import os
from decimal import Decimal

import pytest
from pydantic import BaseModel

from secondmind.config import DEFAULT_RESOURCES_DIR, ModelRef, Step, read_price_table
from secondmind.providers import (
    ChatMessage,
    ChatResult,
    FakeOutcome,
    FakeProvider,
    ModelRouter,
    ProviderAdapter,
    ResiliencePolicy,
    TextDelta,
    ToolCall,
    ToolSpec,
)
from secondmind.providers.adapters import AnthropicAdapter, OpenAIAdapter
from tests.unit.providers.helpers import routing

PRICES = read_price_table(DEFAULT_RESOURCES_DIR / "config" / "prices.yaml")

LIVE_MODELS = {
    "anthropic": os.environ.get("LIVE_ANTHROPIC_MODEL", "claude-opus-5"),
    "openai": os.environ.get("LIVE_OPENAI_MODEL", "gpt-6-sol"),
}
LIVE_EMBED_MODEL = os.environ.get("LIVE_OPENAI_EMBED_MODEL", "text-embedding-3-small")

ADD_TOOL = ToolSpec(
    name="add",
    description="Add two integers and return the sum.",
    parameters={
        "type": "object",
        "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
        "required": ["a", "b"],
        "additionalProperties": False,
    },
)


class Capital(BaseModel):
    country: str
    capital: str


def _fake() -> FakeProvider:
    fake = FakeProvider("fake")
    fake.script.add(
        FakeOutcome(structured={"country": "France", "capital": "Paris"}), contains="capital"
    )
    fake.script.add(
        FakeOutcome(tool_calls=(ToolCall(id="call_1", name="add", arguments={"a": 2, "b": 3}),)),
        contains="add tool",
        after_tool_result=False,
    )
    fake.script.add(FakeOutcome(text="The sum is 5."), after_tool_result=True)
    return fake


def _adapter(provider: str) -> tuple[ProviderAdapter, str]:
    if provider == "fake":
        return _fake(), "fake-chat"
    key_env = f"{provider.upper()}_API_KEY"
    key = os.environ.get(key_env, "")
    if not key:
        pytest.skip(f"{key_env} not set")
    if provider == "anthropic":
        return AnthropicAdapter("anthropic", api_key=key), LIVE_MODELS[provider]
    return OpenAIAdapter("openai", api_key=key), LIVE_MODELS[provider]


PROVIDERS = [
    pytest.param("fake", id="fake"),
    pytest.param("anthropic", id="anthropic", marks=pytest.mark.live),
    pytest.param("openai", id="openai", marks=pytest.mark.live),
]


def _router(provider: str, step: Step = Step.ANSWER) -> tuple[ModelRouter, ModelRef]:
    adapter, model = _adapter(provider)
    ref = ModelRef(provider=provider, model=model)
    r = ModelRouter(
        routing=routing(str(ref), timeout_s=120, step=step),
        prices=PRICES,
        adapters={provider: adapter},
        policy=ResiliencePolicy(max_retries=1),
    )
    return r, ref


@pytest.mark.parametrize("provider", PROVIDERS)
async def test_streaming_yields_text_then_usage(provider: str) -> None:
    r, ref = _router(provider)
    deltas: list[str] = []
    final: ChatResult | None = None
    async for event in r.stream(
        Step.ANSWER,
        system="Reply in one short sentence.",
        messages=[ChatMessage.user("Say hello.")],
    ):
        if isinstance(event, TextDelta):
            deltas.append(event.text)
        else:
            final = event
    assert final is not None
    assert deltas
    assert "".join(deltas) == final.text
    _assert_usage(final.call.usage, ref)
    assert final.call.time_to_first_token_ms is not None


@pytest.mark.parametrize("provider", PROVIDERS)
async def test_structured_output_returns_a_validated_instance(provider: str) -> None:
    r, ref = _router(provider)
    result = await r.structured(
        Step.ANSWER,
        Capital,
        system="Answer with the requested fields only.",
        messages=[ChatMessage.user("What is the capital of France?")],
    )
    assert isinstance(result.value, Capital)
    assert result.value.capital.lower() == "paris"
    _assert_usage(result.call.usage, ref)


@pytest.mark.parametrize("provider", PROVIDERS)
async def test_tool_call_round_trip(provider: str) -> None:
    r, ref = _router(provider)
    history = [ChatMessage.user("Use the add tool to add 2 and 3, then tell me the result.")]
    first = await r.chat(Step.ANSWER, system=None, messages=history, tools=[ADD_TOOL])
    assert first.tool_calls, "model should call the add tool"
    call = first.tool_calls[0]
    assert call.name == "add"
    assert call.arguments == {"a": 2, "b": 3}
    _assert_usage(first.call.usage, ref)

    history += [
        ChatMessage(role="assistant", content=first.text, tool_calls=first.tool_calls),
        ChatMessage.tool_result(call.id, str(call.arguments["a"] + call.arguments["b"])),
    ]
    second = await r.chat(Step.ANSWER, system=None, messages=history, tools=[ADD_TOOL])
    assert "5" in second.text
    assert not second.tool_calls


@pytest.mark.parametrize(
    "provider",
    [pytest.param("fake", id="fake"), pytest.param("openai", id="openai", marks=pytest.mark.live)],
)
async def test_embeddings_return_vectors_and_usage(provider: str) -> None:
    adapter, _ = _adapter(provider)
    model = "fake-embed" if provider == "fake" else LIVE_EMBED_MODEL
    ref = ModelRef(provider=provider, model=model)
    r = ModelRouter(
        routing=routing(str(ref), step=Step.EMBED),
        prices=PRICES,
        adapters={provider: adapter},
        policy=ResiliencePolicy(max_retries=1),
    )
    result = await r.embed(["a gift idea", "a deadline"])
    assert len(result.vectors) == 2
    assert len(result.vectors[0]) > 100
    assert result.call.usage.input_tokens > 0
    assert result.call.usage.cost_usd >= 0


def _assert_usage(usage, ref: ModelRef) -> None:  # type: ignore[no-untyped-def]
    assert usage.provider == ref.provider
    assert usage.model == ref.model
    assert usage.input_tokens > 0
    assert usage.output_tokens > 0
    assert usage.latency_ms >= 0
    assert usage.price_version == PRICES.version
    expected = PRICES.cost(
        ref,
        input_tokens=usage.input_tokens,
        cached_input_tokens=usage.cached_input_tokens,
        output_tokens=usage.output_tokens,
    )
    assert usage.cost_usd == expected
    assert usage.cost_usd > Decimal(0)
