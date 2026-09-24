"""The provider-agnostic contract (FR-14.3): messages, tools, and what adapters return.

Adapters translate these to and from each SDK. Nothing above the router sees SDK types.
"""

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict

from secondmind.config import Effort


class ToolCall(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    name: str
    arguments: dict[str, Any]


class ToolSpec(BaseModel):
    """A tool the model may call. ``parameters`` is a JSON Schema object."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    description: str
    parameters: dict[str, Any]


class ChatMessage(BaseModel):
    """One conversation message. ``assistant`` messages may carry tool calls; ``tool``
    messages carry the result of one call."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    role: Literal["user", "assistant", "tool"]
    content: str = ""
    tool_calls: list[ToolCall] = []
    tool_call_id: str | None = None

    @classmethod
    def user(cls, content: str) -> "ChatMessage":
        return cls(role="user", content=content)

    @classmethod
    def tool_result(cls, call_id: str, content: str) -> "ChatMessage":
        return cls(role="tool", content=content, tool_call_id=call_id)


@dataclass(frozen=True, slots=True)
class AdapterRequest:
    """What the router asks an adapter for; ``step`` is informational (the fake keys on it)."""

    step: str
    model: str
    system: str | None
    messages: Sequence[ChatMessage]
    max_output_tokens: int
    effort: Effort | None = None
    tools: Sequence[ToolSpec] = ()


@dataclass(frozen=True, slots=True)
class RawUsage:
    """Token counts as the adapter normalised them: ``input_tokens`` excludes cached reads."""

    input_tokens: int
    output_tokens: int
    cached_input_tokens: int = 0


@dataclass(frozen=True, slots=True)
class AdapterReply:
    text: str
    usage: RawUsage
    tool_calls: list[ToolCall] = field(default_factory=list)
    stop_reason: str | None = None


@dataclass(frozen=True, slots=True)
class TextDelta:
    text: str


@dataclass(frozen=True, slots=True)
class KeepAlive:
    """Non-text stream activity (e.g. thinking); resets the idle timeout, never shown."""


@dataclass(frozen=True, slots=True)
class StreamEnd:
    reply: AdapterReply


AdapterStreamEvent = TextDelta | KeepAlive | StreamEnd


@dataclass(frozen=True, slots=True)
class AdapterStructured[T: BaseModel]:
    value: T
    usage: RawUsage


@dataclass(frozen=True, slots=True)
class AdapterEmbedding:
    vectors: list[list[float]]
    usage: RawUsage


class ProviderAdapter(Protocol):
    """One provider behind the normalised contract. Adapters raise ``ProviderError``."""

    @property
    def name(self) -> str: ...

    def stream_chat(self, request: AdapterRequest) -> AsyncIterator[AdapterStreamEvent]: ...

    async def chat(self, request: AdapterRequest) -> AdapterReply: ...

    async def structured[T: BaseModel](
        self, request: AdapterRequest, schema: type[T]
    ) -> AdapterStructured[T]: ...

    async def embed(self, model: str, texts: Sequence[str]) -> AdapterEmbedding: ...

    async def aclose(self) -> None: ...
