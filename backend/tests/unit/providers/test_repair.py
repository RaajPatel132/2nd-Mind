"""R.3: a structured reply that breaks the schema (an enum value the model made up) is sent back
once, with where it was wrong, before the step moves to its fallback."""

from pydantic import BaseModel

from secondmind.config import Step
from secondmind.providers import ChatMessage, FakeOutcome
from tests.unit.providers.helpers import fakes, router


class Capital(BaseModel):
    country: str
    capital: str


GOOD = FakeOutcome(structured={"country": "France", "capital": "Paris"})
BAD = FakeOutcome(structured={"country": "France"})  # no capital
ASK = [ChatMessage.user("capital of France?")]


async def test_an_invalid_reply_is_corrected_once_by_the_same_model() -> None:
    adapters = fakes("primary", "backup")
    adapters["primary"].script.add(BAD, GOOD)
    r = router(adapters, "primary:m", "backup:m")

    result = await r.structured(Step.ANSWER, Capital, system=None, messages=ASK)

    assert result.value.capital == "Paris"
    assert result.call.provider == "primary"
    assert result.call.fallback is None  # no fallback: the same model fixed it
    first, second = adapters["primary"].requests
    assert len(second.messages) == len(first.messages) + 2
    assert second.messages[-2].role == "assistant"
    feedback = second.messages[-1].content
    assert "capital (missing)" in feedback  # where it was wrong, by path and type
    assert adapters["backup"].requests == []


async def test_a_reply_still_invalid_after_the_correction_goes_to_the_fallback() -> None:
    adapters = fakes("primary", "backup")
    adapters["primary"].script.add(BAD, BAD)
    adapters["backup"].script.add(GOOD)
    r = router(adapters, "primary:m", "backup:m")

    result = await r.structured(Step.ANSWER, Capital, system=None, messages=ASK)

    assert result.call.provider == "backup"
    assert len(adapters["primary"].requests) == 2  # asked once, corrected once, no more
    assert result.call.fallback is not None
    assert "invalid_output" in result.call.fallback.reason
