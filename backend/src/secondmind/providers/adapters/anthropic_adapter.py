"""Anthropic adapter: Messages API streaming, structured output (``messages.parse``) and tools."""

import json
from collections.abc import AsyncIterator, Sequence
from types import UnionType
from typing import Any, NoReturn, Union, get_args, get_origin

import anthropic
from pydantic import BaseModel, ValidationError

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
    validation_summary,
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
        # Schemas the API refused as too large for constrained decoding.
        self._too_large: set[str] = set()
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
        """Structured output through constrained decoding (``output_config.format``). A schema
        whose grammar the API refuses as too large goes through a tool call instead, from then
        on for this adapter (found live on the extract schema, R.3)."""
        if schema.__name__ in self._too_large:
            return await self._structured_by_tool(request, schema)
        params = self._params(request)
        params.pop("tools", None)
        try:
            message = await self._client.messages.parse(**params, output_format=schema)
        except anthropic.BadRequestError as exc:
            if not _grammar_too_large(exc):
                self._raise(exc)
            self._too_large.add(schema.__name__)
            return await self._structured_by_tool(request, schema)
        except anthropic.AnthropicError as exc:
            self._raise(exc)
        except ValidationError as exc:  # a reply cut short (max_tokens) doesn't parse
            raise ProviderError(
                ProviderErrorKind.INVALID_OUTPUT,
                f"no valid {schema.__name__} in the response: {validation_summary(exc)}",
                provider=self._name,
            ) from exc
        value = message.parsed_output
        if not isinstance(value, schema):
            raise ProviderError(
                ProviderErrorKind.INVALID_OUTPUT,
                f"no valid {schema.__name__} in the response (stop: {message.stop_reason})",
                provider=self._name,
            )
        return AdapterStructured(value=value, usage=_usage(message.usage))

    async def _structured_by_tool[T: BaseModel](
        self, request: AdapterRequest, schema: type[T]
    ) -> AdapterStructured[T]:
        """The schema as the input of one tool the model must call; the input is validated
        here, with any nullable field or list it left out filled in (not constrained)."""
        params = self._params(request)
        params.pop("output_config", None)
        params["tools"] = [
            {
                "name": _RECORD_TOOL,
                "description": f"Record the result ({schema.__name__}). Call it exactly once.",
                "input_schema": schema.model_json_schema(),
            }
        ]
        if _forced_tool_rejected(request.model):
            params["tool_choice"] = {"type": "auto"}
            system = params.get("system")
            note = f"Answer only by calling the {_RECORD_TOOL} tool once."
            params["system"] = f"{system}\n\n{note}" if isinstance(system, str) else note
        else:
            params["tool_choice"] = {"type": "tool", "name": _RECORD_TOOL}
            if _thinks_by_default(request.model):
                params["thinking"] = {"type": "disabled"}  # forced tool use can't think first
        try:
            message = await self._client.messages.create(**params)
        except anthropic.AnthropicError as exc:
            self._raise(exc)
        call = next(
            (b for b in message.content if b.type == "tool_use" and b.name == _RECORD_TOOL), None
        )
        if call is None:
            raise ProviderError(
                ProviderErrorKind.INVALID_OUTPUT,
                f"no {_RECORD_TOOL} call in the response (stop: {message.stop_reason})",
                provider=self._name,
            )
        try:
            value = schema.model_validate(fill_omitted(schema, call.input))
        except ValidationError as exc:
            raise ProviderError(
                ProviderErrorKind.INVALID_OUTPUT,
                f"the {schema.__name__} the model gave is invalid: {validation_summary(exc)}",
                provider=self._name,
                raw_output=json.dumps(call.input, ensure_ascii=False),
            ) from exc
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


_RECORD_TOOL = "record_result"


def _grammar_too_large(exc: anthropic.BadRequestError) -> bool:
    text = str(exc).lower()
    return "grammar is too large" in text or "schema is too complex" in text


def _forced_tool_rejected(model: str) -> bool:
    # Fable 5.1 and Opus 5.5 refuse tool_choice "tool"/"any" with a 400.
    return model.startswith(("claude-fable-5-1", "claude-opus-5-5", "claude-mythos"))


def _thinks_by_default(model: str) -> bool:
    return not model.startswith(("claude-haiku", "claude-3"))


def fill_omitted(schema: type[BaseModel], data: object) -> object:
    """``data`` with every field it left out that may be null set to null and every list set
    to empty, recursively: what an unconstrained model most often skips."""
    if not isinstance(data, dict):
        return data
    out = dict(data)
    for name, field in schema.model_fields.items():
        annotation = field.annotation
        if name not in out:
            if _allows_none(annotation):
                out[name] = None
            elif get_origin(annotation) is list:
                out[name] = []
            continue
        nested = _model_of(annotation)
        if nested is not None:
            if get_origin(annotation) is list and isinstance(out[name], list):
                out[name] = [fill_omitted(nested, v) for v in out[name]]
            else:
                out[name] = fill_omitted(nested, out[name])
    return out


def _allows_none(annotation: object) -> bool:
    return get_origin(annotation) in (Union, UnionType) and type(None) in get_args(annotation)


def _model_of(annotation: object) -> type[BaseModel] | None:
    """The model inside ``Model``, ``Model | None`` or ``list[Model]``, if any."""
    candidates = [annotation, *get_args(annotation)]
    for c in candidates:
        if isinstance(c, type) and issubclass(c, BaseModel):
            return c
    return None


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
    if _out_of_credit(exc):
        return ProviderErrorKind.CREDIT
    # Most specific first: APITimeoutError subclasses APIConnectionError.
    for cls, kind in _KINDS:
        if isinstance(exc, cls):
            return kind
    if isinstance(exc, anthropic.APIStatusError):
        return ProviderErrorKind.SERVER if exc.status_code >= 500 else ProviderErrorKind.BAD_REQUEST
    return ProviderErrorKind.INVALID_OUTPUT


_KINDS: tuple[tuple[type[Exception] | tuple[type[Exception], ...], ProviderErrorKind], ...] = (
    (anthropic.APITimeoutError, ProviderErrorKind.TIMEOUT),
    (anthropic.APIConnectionError, ProviderErrorKind.CONNECTION),
    (anthropic.RateLimitError, ProviderErrorKind.RATE_LIMITED),
    ((anthropic.AuthenticationError, anthropic.PermissionDeniedError), ProviderErrorKind.AUTH),
)


def _out_of_credit(exc: anthropic.AnthropicError) -> bool:
    """A billing problem: 402 ``billing_error``, or the 400 "credit balance is too low"."""
    if not isinstance(exc, anthropic.APIStatusError):
        return False
    if exc.status_code == 402 or getattr(exc, "type", None) == "billing_error":
        return True
    return exc.status_code == 400 and "credit balance" in str(exc).lower()
