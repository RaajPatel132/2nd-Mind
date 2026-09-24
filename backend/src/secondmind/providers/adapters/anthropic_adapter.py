"""Anthropic adapter: Messages API streaming, structured output (``messages.parse``) and tools."""

from collections.abc import AsyncIterator, Sequence
from typing import Any, NoReturn

import anthropic
from pydantic import BaseModel

from secondmind.providers import (
    AdapterEmbedding,
    AdapterReply,
    AdapterRequest,
    AdapterStreamEvent,
    AdapterStructured,
    ChatMessage,
    KeepAlive,
    ProviderError,
    ProviderErrorKind,
    RawUsage,
    StreamEnd,
    TextDelta,
    ToolCall,
)

# Hard ceiling for one HTTP request; the router's per-step timeouts are the real limits.
_SDK_TIMEOUT_S = 600.0


class AnthropicAdapter:
    def __init__(
        self,
        name: str,
        *,
        api_key: str,
        base_url: str | None = None,
        http_client: Any | None = None,
    ) -> None:
        self._name = name
        self._client = anthropic.AsyncAnthropic(
            api_key=api_key,
            base_url=base_url,
            max_retries=0,
            timeout=_SDK_TIMEOUT_S,
            http_client=http_client,
        )

    @property
    def name(self) -> str:
        return self._name

    async def stream_chat(self, request: AdapterRequest) -> AsyncIterator[AdapterStreamEvent]:
        try:
            async with self._client.messages.stream(**self._params(request)) as stream:
                async for event in stream:
                    if event.type == "content_block_delta" and event.delta.type == "text_delta":
                        yield TextDelta(event.delta.text)
                    else:
                        yield KeepAlive()
                final = await stream.get_final_message()
        except anthropic.AnthropicError as exc:
            self._raise(exc)
        yield StreamEnd(self._reply(final))

    async def chat(self, request: AdapterRequest) -> AdapterReply:
        try:
            message = await self._client.messages.create(**self._params(request))
        except anthropic.AnthropicError as exc:
            self._raise(exc)
        return self._reply(message)

    async def structured[T: BaseModel](
        self, request: AdapterRequest, schema: type[T]
    ) -> AdapterStructured[T]:
        params = self._params(request)
        params.pop("tools", None)
        try:
            message = await self._client.messages.parse(**params, output_format=schema)
        except anthropic.AnthropicError as exc:
            self._raise(exc)
        value = message.parsed_output
        if not isinstance(value, schema):
            raise ProviderError(
                ProviderErrorKind.INVALID_OUTPUT,
                f"no valid {schema.__name__} in the response (stop: {message.stop_reason})",
                provider=self._name,
            )
        return AdapterStructured(value=value, usage=_usage(message.usage))

    async def embed(
        self, model: str, texts: Sequence[str], dimensions: int | None = None
    ) -> AdapterEmbedding:
        raise ProviderError(
            ProviderErrorKind.UNSUPPORTED,
            "Anthropic has no embeddings API; route the embed step to another provider",
            provider=self._name,
        )

    async def aclose(self) -> None:
        await self._client.close()

    # ------------------------------------------------------------------ mapping

    def _params(self, request: AdapterRequest) -> dict[str, Any]:
        params: dict[str, Any] = {
            "model": request.model,
            "max_tokens": request.max_output_tokens,
            "messages": _messages(request.messages),
        }
        if request.cache_prefix:
            blocks: list[dict[str, Any]] = [
                {
                    "type": "text",
                    "text": request.cache_prefix,
                    "cache_control": {"type": "ephemeral"},
                }
            ]
            if request.system:
                blocks.append({"type": "text", "text": request.system})
            params["system"] = blocks
        elif request.system:
            params["system"] = request.system
        if request.tools:
            params["tools"] = [
                {"name": t.name, "description": t.description, "input_schema": t.parameters}
                for t in request.tools
            ]
        if request.effort and _supports_effort(request.model):
            params["output_config"] = {"effort": request.effort}
        return params

    def _reply(self, message: Any) -> AdapterReply:
        text = "".join(b.text for b in message.content if b.type == "text")
        calls = [
            ToolCall(id=b.id, name=b.name, arguments=dict(b.input))
            for b in message.content
            if b.type == "tool_use"
        ]
        return AdapterReply(
            text=text,
            tool_calls=calls,
            stop_reason=message.stop_reason,
            usage=_usage(message.usage),
        )

    def _raise(self, exc: anthropic.AnthropicError) -> NoReturn:
        raise ProviderError(
            _kind(exc),
            str(exc) or type(exc).__name__,
            provider=self._name,
            status_code=getattr(exc, "status_code", None),
        ) from exc


def _supports_effort(model: str) -> bool:
    # Effort (output_config.effort) errors on Haiku 4.5 and older generations.
    return not model.startswith(("claude-haiku", "claude-3"))


def _messages(messages: Sequence[ChatMessage]) -> list[dict[str, Any]]:
    """Map to Anthropic's shape: tool results become ``tool_result`` blocks in a user turn."""
    out: list[dict[str, Any]] = []
    for m in messages:
        if m.role == "tool":
            block = {"type": "tool_result", "tool_use_id": m.tool_call_id, "content": m.content}
            if out and out[-1]["role"] == "user" and isinstance(out[-1]["content"], list):
                out[-1]["content"].append(block)
            else:
                out.append({"role": "user", "content": [block]})
        elif m.role == "assistant" and m.tool_calls:
            content: list[dict[str, Any]] = []
            if m.content:
                content.append({"type": "text", "text": m.content})
            content.extend(
                {"type": "tool_use", "id": c.id, "name": c.name, "input": c.arguments}
                for c in m.tool_calls
            )
            out.append({"role": "assistant", "content": content})
        else:
            out.append({"role": m.role, "content": m.content})
    return out


def _usage(usage: Any) -> RawUsage:
    cache_write = getattr(usage, "cache_creation_input_tokens", None) or 0
    cache_read = getattr(usage, "cache_read_input_tokens", None) or 0
    return RawUsage(
        input_tokens=int(usage.input_tokens) + int(cache_write),
        cached_input_tokens=int(cache_read),
        output_tokens=int(usage.output_tokens),
    )


def _kind(exc: anthropic.AnthropicError) -> ProviderErrorKind:
    # Most specific first: APITimeoutError subclasses APIConnectionError.
    if isinstance(exc, anthropic.APITimeoutError):
        return ProviderErrorKind.TIMEOUT
    if isinstance(exc, anthropic.APIConnectionError):
        return ProviderErrorKind.CONNECTION
    if isinstance(exc, anthropic.RateLimitError):
        return ProviderErrorKind.RATE_LIMITED
    if isinstance(exc, (anthropic.AuthenticationError, anthropic.PermissionDeniedError)):
        return ProviderErrorKind.AUTH
    if isinstance(exc, anthropic.APIStatusError):
        return ProviderErrorKind.SERVER if exc.status_code >= 500 else ProviderErrorKind.BAD_REQUEST
    return ProviderErrorKind.INVALID_OUTPUT
