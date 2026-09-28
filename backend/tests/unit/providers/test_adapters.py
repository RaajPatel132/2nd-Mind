"""Adapter mapping over a mocked HTTP transport: no network, real SDK request building."""

import json
from typing import Any

import httpx2
import pytest
from pydantic import BaseModel

from secondmind.config import ResolvedProvider
from secondmind.providers import (
    AdapterRequest,
    ChatMessage,
    ProviderError,
    ProviderErrorKind,
    ToolCall,
    ToolSpec,
)
from secondmind.providers.adapters import AnthropicAdapter, OpenAIAdapter, build_adapter

COMPLETION = {
    "id": "chatcmpl-1",
    "object": "chat.completion",
    "created": 1,
    "model": "qwen-small",
    "choices": [
        {
            "index": 0,
            "finish_reason": "stop",
            "message": {"role": "assistant", "content": "hi from vllm"},
        }
    ],
    "usage": {
        "prompt_tokens": 12,
        "completion_tokens": 4,
        "total_tokens": 16,
        "prompt_tokens_details": {"cached_tokens": 2},
    },
}


def _request(**kw: Any) -> AdapterRequest:
    base: dict[str, Any] = {
        "step": "answer",
        "model": "qwen-small",
        "system": "be brief",
        "messages": [ChatMessage.user("hello")],
        "max_output_tokens": 64,
        "effort": "low",
    }
    return AdapterRequest(**(base | kw))


async def test_openai_compatible_base_url_receives_the_request() -> None:
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(200, json=COMPLETION)

    client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    adapter = OpenAIAdapter(
        "vllm", api_key=None, base_url="http://vllm.test:8000/v1", http_client=client
    )

    reply = await adapter.chat(_request())

    assert str(seen[0].url) == "http://vllm.test:8000/v1/chat/completions"
    body = json.loads(seen[0].content)
    assert body["model"] == "qwen-small"
    assert "reasoning_effort" not in body  # vendor-specific, not sent to compatible servers
    assert body["messages"][0] == {"role": "system", "content": "be brief"}
    assert reply.text == "hi from vllm"
    assert (reply.usage.input_tokens, reply.usage.cached_input_tokens) == (10, 2)
    assert reply.usage.output_tokens == 4


def test_build_adapter_registers_a_compatible_provider_from_config() -> None:
    provider = ResolvedProvider(
        name="vllm", kind="openai", base_url="http://vllm.test:8000/v1", has_credentials=True
    )
    adapter = build_adapter(provider, {})
    assert isinstance(adapter, OpenAIAdapter)
    assert adapter.name == "vllm"
    assert adapter.base_url == "http://vllm.test:8000/v1/"


@pytest.mark.parametrize(
    ("status", "kind"),
    [
        (429, ProviderErrorKind.RATE_LIMITED),
        (500, ProviderErrorKind.SERVER),
        (400, ProviderErrorKind.BAD_REQUEST),
        (401, ProviderErrorKind.AUTH),
    ],
)
async def test_openai_errors_map_to_normalised_kinds(status: int, kind: ProviderErrorKind) -> None:
    client = httpx2.AsyncClient(
        transport=httpx2.MockTransport(
            lambda r: httpx2.Response(status, json={"error": {"message": "x"}})
        )
    )
    adapter = OpenAIAdapter("openai", api_key="k", http_client=client)
    with pytest.raises(ProviderError) as exc:
        await adapter.chat(_request())
    assert exc.value.kind is kind
    assert exc.value.status_code == status


async def test_anthropic_maps_tool_results_and_usage() -> None:
    seen: list[dict[str, Any]] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(json.loads(request.content))
        return httpx2.Response(
            200,
            json={
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "model": "claude-opus-5",
                "content": [{"type": "text", "text": "The sum is 5."}],
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {
                    "input_tokens": 20,
                    "output_tokens": 6,
                    "cache_read_input_tokens": 100,
                    "cache_creation_input_tokens": 5,
                },
            },
        )

    client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    adapter = AnthropicAdapter("anthropic", api_key="k", http_client=client)
    call = ToolCall(id="toolu_1", name="add", arguments={"a": 2, "b": 3})
    reply = await adapter.chat(
        _request(
            model="claude-opus-5",
            messages=[
                ChatMessage.user("add 2 and 3"),
                ChatMessage(role="assistant", content="", tool_calls=[call]),
                ChatMessage.tool_result("toolu_1", "5"),
            ],
        )
    )

    body = seen[0]
    assert body["system"] == "be brief"
    assert body["output_config"] == {"effort": "low"}
    assert body["messages"][1]["content"][0]["type"] == "tool_use"
    assert body["messages"][2] == {
        "role": "user",
        "content": [{"type": "tool_result", "tool_use_id": "toolu_1", "content": "5"}],
    }
    assert reply.text == "The sum is 5."
    # uncached input includes cache writes; cache reads are reported separately
    assert (reply.usage.input_tokens, reply.usage.cached_input_tokens) == (25, 100)


async def test_anthropic_effort_is_not_sent_to_haiku() -> None:
    seen: list[dict[str, Any]] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(json.loads(request.content))
        return httpx2.Response(529, json={"type": "error", "error": {"type": "overloaded_error"}})

    adapter = AnthropicAdapter(
        "anthropic",
        api_key="k",
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
    )
    with pytest.raises(ProviderError) as exc:
        await adapter.chat(_request(model="claude-haiku-4-5"))
    assert "output_config" not in seen[0]
    assert exc.value.kind is ProviderErrorKind.SERVER
    assert exc.value.retryable


@pytest.mark.parametrize(
    ("status", "body"),
    [
        (429, {"error": {"message": "You exceeded your current quota", "code": "insufficient_quota",
                         "type": "insufficient_quota"}}),
        (402, {"error": {"message": "Payment required"}}),
    ],
)  # fmt: skip
async def test_openai_out_of_credit_is_a_credit_error_not_a_rate_limit(
    status: int, body: dict[str, Any]
) -> None:
    client = httpx2.AsyncClient(
        transport=httpx2.MockTransport(lambda r: httpx2.Response(status, json=body))
    )
    adapter = OpenAIAdapter("openai", api_key="k", http_client=client)
    with pytest.raises(ProviderError) as exc:
        await adapter.chat(_request())
    assert exc.value.kind is ProviderErrorKind.CREDIT
    assert not exc.value.retryable


@pytest.mark.parametrize(
    ("status", "body"),
    [
        (400, {"type": "error", "error": {"type": "invalid_request_error", "message":
               "Your credit balance is too low to access the Anthropic API."}}),
        (402, {"type": "error", "error": {"type": "billing_error", "message": "Billing issue"}}),
    ],
)  # fmt: skip
async def test_anthropic_out_of_credit_is_a_credit_error(status: int, body: dict[str, Any]) -> None:
    adapter = AnthropicAdapter(
        "anthropic",
        api_key="k",
        http_client=httpx2.AsyncClient(
            transport=httpx2.MockTransport(lambda r: httpx2.Response(status, json=body))
        ),
    )
    with pytest.raises(ProviderError) as exc:
        await adapter.chat(_request(model="claude-haiku-4-5"))
    assert exc.value.kind is ProviderErrorKind.CREDIT
    assert not exc.value.retryable


@pytest.mark.parametrize(
    ("tools", "effort", "sent"),
    [((), "low", "low"), ((), None, None), (("add",), "low", "none"), (("add",), None, "none")],
)
async def test_openai_turns_reasoning_off_when_a_request_carries_tools(
    tools: tuple[str, ...], effort: str | None, sent: str | None
) -> None:
    """Found live (R.3 B1): reasoning models refuse function tools on Chat Completions unless
    reasoning_effort is "none", even when no effort is sent."""
    seen: list[dict[str, Any]] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(json.loads(request.content))
        return httpx2.Response(200, json=COMPLETION)

    adapter = OpenAIAdapter(
        "openai",
        api_key="k",
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
    )
    specs = [
        ToolSpec(name=t, description="d", parameters={"type": "object", "properties": {}})
        for t in tools
    ]
    await adapter.chat(_request(model="gpt-6-luna", effort=effort, tools=specs))
    assert seen[0].get("reasoning_effort") == sent


class _Inner(BaseModel):
    name: str
    note: str | None


class _Big(BaseModel):
    items: list[_Inner]
    label: str | None
    tags: list[str]


def _anthropic_message(content: list[dict[str, Any]], stop: str = "tool_use") -> dict[str, Any]:
    return {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": "claude-haiku-4-5",
        "content": content,
        "stop_reason": stop,
        "stop_sequence": None,
        "usage": {"input_tokens": 30, "output_tokens": 9},
    }


async def test_anthropic_uses_a_tool_call_when_the_schema_grammar_is_too_large() -> None:
    """Found live (R.3 B1): Haiku 4.5 refuses the extract schema's grammar as too large."""
    seen: list[dict[str, Any]] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        seen.append(body)
        if "output_config" in body and "format" in body["output_config"]:
            return httpx2.Response(
                400,
                json={
                    "type": "error",
                    "error": {
                        "type": "invalid_request_error",
                        "message": "The compiled grammar is too large, which would cause "
                        "performance issues. Simplify your tool schemas.",
                    },
                },
            )
        tool = {"type": "tool_use", "id": "t1", "name": "record_result",
                "input": {"items": [{"name": "a"}]}}  # fmt: skip
        return httpx2.Response(200, json=_anthropic_message([tool]))

    adapter = AnthropicAdapter(
        "anthropic",
        api_key="k",
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
    )
    first = await adapter.structured(_request(model="claude-haiku-4-5"), _Big)
    assert first.value == _Big(items=[_Inner(name="a", note=None)], label=None, tags=[])
    assert first.usage.output_tokens == 9
    assert seen[1]["tool_choice"] == {"type": "tool", "name": "record_result"}
    assert seen[1]["tools"][0]["input_schema"]["properties"]["items"]
    # The adapter remembers: the next call goes straight to the tool.
    await adapter.structured(_request(model="claude-haiku-4-5"), _Big)
    assert len(seen) == 3


async def test_anthropic_reports_a_cut_off_structured_reply_as_invalid_output() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200, json=_anthropic_message([{"type": "text", "text": '{"na'}], stop="max_tokens")
        )

    adapter = AnthropicAdapter(
        "anthropic",
        api_key="k",
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
    )
    with pytest.raises(ProviderError) as exc:
        await adapter.structured(_request(model="claude-haiku-4-5"), _Inner)
    assert exc.value.kind is ProviderErrorKind.INVALID_OUTPUT
