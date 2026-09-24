"""OpenAI adapter: Chat Completions streaming, structured output, tools and embeddings.

Chat Completions (not the Responses API) is used on purpose: it is the surface OpenAI-compatible
servers such as vLLM implement, so a self-hosted model registers through config alone by
giving this adapter a ``base_url`` (FR-14.7).
"""

import json
from collections.abc import AsyncIterator, Sequence
from typing import Any, NoReturn

import openai
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

_SDK_TIMEOUT_S = 600.0
_KEYLESS = "not-needed"


class OpenAIAdapter:
    def __init__(
        self,
        name: str,
        *,
        api_key: str | None,
        base_url: str | None = None,
        http_client: Any | None = None,
    ) -> None:
        self._name = name
        self._compatible = base_url is not None
        self._client = openai.AsyncOpenAI(
            api_key=api_key or _KEYLESS,
            base_url=base_url,
            max_retries=0,
            timeout=_SDK_TIMEOUT_S,
            http_client=http_client,
        )

    @property
    def name(self) -> str:
        return self._name

    @property
    def base_url(self) -> str:
        return str(self._client.base_url)

    async def stream_chat(self, request: AdapterRequest) -> AsyncIterator[AdapterStreamEvent]:
        text_parts: list[str] = []
        calls: dict[int, dict[str, str]] = {}
        usage: RawUsage | None = None
        finish: str | None = None
        try:
            stream = await self._client.chat.completions.create(
                **self._params(request), stream=True, stream_options={"include_usage": True}
            )
            async for chunk in stream:
                if chunk.usage is not None:
                    usage = _usage(chunk.usage)
                if not chunk.choices:
                    yield KeepAlive()
                    continue
                choice = chunk.choices[0]
                finish = choice.finish_reason or finish
                delta = choice.delta
                for tc in delta.tool_calls or []:
                    slot = calls.setdefault(tc.index, {"id": "", "name": "", "arguments": ""})
                    slot["id"] = tc.id or slot["id"]
                    if tc.function is not None:
                        slot["name"] = tc.function.name or slot["name"]
                        slot["arguments"] += tc.function.arguments or ""
                if delta.content:
                    text_parts.append(delta.content)
                    yield TextDelta(delta.content)
                else:
                    yield KeepAlive()
        except openai.OpenAIError as exc:
            self._raise(exc)
        if usage is None:
            raise ProviderError(
                ProviderErrorKind.INVALID_OUTPUT, "stream ended without usage", provider=self._name
            )
        tool_calls = [
            self._tool_call(c["id"], c["name"], c["arguments"]) for _, c in sorted(calls.items())
        ]
        yield StreamEnd(
            AdapterReply(
                text="".join(text_parts), tool_calls=tool_calls, stop_reason=finish, usage=usage
            )
        )

    async def chat(self, request: AdapterRequest) -> AdapterReply:
        try:
            completion = await self._client.chat.completions.create(**self._params(request))
        except openai.OpenAIError as exc:
            self._raise(exc)
        choice = completion.choices[0]
        calls = [
            self._tool_call(c.id, c.function.name, c.function.arguments)
            for c in (choice.message.tool_calls or [])
            if c.type == "function"
        ]
        if completion.usage is None:
            raise ProviderError(
                ProviderErrorKind.INVALID_OUTPUT, "response without usage", provider=self._name
            )
        return AdapterReply(
            text=choice.message.content or "",
            tool_calls=calls,
            stop_reason=choice.finish_reason,
            usage=_usage(completion.usage),
        )

    async def structured[T: BaseModel](
        self, request: AdapterRequest, schema: type[T]
    ) -> AdapterStructured[T]:
        params = self._params(request)
        params.pop("tools", None)
        try:
            completion = await self._client.chat.completions.parse(**params, response_format=schema)
        except openai.OpenAIError as exc:
            self._raise(exc)
        message = completion.choices[0].message
        value = message.parsed
        if not isinstance(value, schema) or completion.usage is None:
            reason = message.refusal or "unparseable output"
            raise ProviderError(
                ProviderErrorKind.INVALID_OUTPUT,
                f"no valid {schema.__name__} in the response: {reason}",
                provider=self._name,
            )
        return AdapterStructured(value=value, usage=_usage(completion.usage))

    async def embed(
        self, model: str, texts: Sequence[str], dimensions: int | None = None
    ) -> AdapterEmbedding:
        extra: dict[str, Any] = {"dimensions": dimensions} if dimensions is not None else {}
        try:
            response = await self._client.embeddings.create(model=model, input=list(texts), **extra)
        except openai.OpenAIError as exc:
            self._raise(exc)
        vectors = [list(d.embedding) for d in sorted(response.data, key=lambda d: d.index)]
        return AdapterEmbedding(
            vectors=vectors,
            usage=RawUsage(input_tokens=response.usage.prompt_tokens, output_tokens=0),
        )

    async def aclose(self) -> None:
        await self._client.close()

    # ------------------------------------------------------------------ mapping

    def _params(self, request: AdapterRequest) -> dict[str, Any]:
        messages: list[dict[str, Any]] = []
        system = request.full_system
        if system:
            # The stable core prefix goes first, so OpenAI's automatic prefix caching can hit.
            messages.append({"role": "system", "content": system})
        messages.extend(_message(m) for m in request.messages)
        params: dict[str, Any] = {
            "model": request.model,
            "messages": messages,
            "max_completion_tokens": request.max_output_tokens,
        }
        if request.tools:
            params["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters,
                    },
                }
                for t in request.tools
            ]
        # Vendor-specific; compatible servers (vLLM etc.) may not accept it.
        if request.effort and not self._compatible:
            params["reasoning_effort"] = request.effort
        return params

    def _tool_call(self, call_id: str, name: str, arguments: str) -> ToolCall:
        try:
            parsed = json.loads(arguments or "{}")
        except json.JSONDecodeError as exc:
            raise ProviderError(
                ProviderErrorKind.INVALID_OUTPUT,
                f"tool call {name!r} has invalid JSON arguments",
                provider=self._name,
            ) from exc
        if not isinstance(parsed, dict):
            raise ProviderError(
                ProviderErrorKind.INVALID_OUTPUT,
                f"tool call {name!r} arguments are not an object",
                provider=self._name,
            )
        return ToolCall(id=call_id, name=name, arguments=parsed)

    def _raise(self, exc: openai.OpenAIError) -> NoReturn:
        raise ProviderError(
            _kind(exc),
            str(exc) or type(exc).__name__,
            provider=self._name,
            status_code=getattr(exc, "status_code", None),
        ) from exc


def _message(m: ChatMessage) -> dict[str, Any]:
    if m.role == "tool":
        return {"role": "tool", "tool_call_id": m.tool_call_id, "content": m.content}
    if m.role == "assistant" and m.tool_calls:
        return {
            "role": "assistant",
            "content": m.content or None,
            "tool_calls": [
                {
                    "id": c.id,
                    "type": "function",
                    "function": {"name": c.name, "arguments": json.dumps(c.arguments)},
                }
                for c in m.tool_calls
            ],
        }
    return {"role": m.role, "content": m.content}


def _usage(usage: Any) -> RawUsage:
    details = getattr(usage, "prompt_tokens_details", None)
    cached = int(getattr(details, "cached_tokens", 0) or 0)
    prompt = int(usage.prompt_tokens)
    return RawUsage(
        input_tokens=max(0, prompt - cached),
        cached_input_tokens=cached,
        output_tokens=int(usage.completion_tokens or 0),
    )


def _kind(exc: openai.OpenAIError) -> ProviderErrorKind:
    if isinstance(exc, openai.APITimeoutError):
        return ProviderErrorKind.TIMEOUT
    if isinstance(exc, openai.APIConnectionError):
        return ProviderErrorKind.CONNECTION
    if isinstance(exc, openai.RateLimitError):
        return ProviderErrorKind.RATE_LIMITED
    if isinstance(exc, (openai.AuthenticationError, openai.PermissionDeniedError)):
        return ProviderErrorKind.AUTH
    if isinstance(exc, openai.APIStatusError):
        return ProviderErrorKind.SERVER if exc.status_code >= 500 else ProviderErrorKind.BAD_REQUEST
    return ProviderErrorKind.INVALID_OUTPUT
