"""Provider errors, normalised across SDKs so resilience logic never special-cases one."""

from dataclasses import dataclass
from enum import StrEnum

from secondmind.core import SecondMindError


class ProviderErrorKind(StrEnum):
    TIMEOUT = "timeout"
    RATE_LIMITED = "rate_limited"
    SERVER = "server"
    CONNECTION = "connection"
    BAD_REQUEST = "bad_request"
    AUTH = "auth"
    INVALID_OUTPUT = "invalid_output"
    UNSUPPORTED = "unsupported"


RETRYABLE_KINDS = frozenset(
    {
        ProviderErrorKind.TIMEOUT,
        ProviderErrorKind.RATE_LIMITED,
        ProviderErrorKind.SERVER,
        ProviderErrorKind.CONNECTION,
    }
)


class ProviderError(SecondMindError):
    """One failed provider call. ``retryable`` is true only for timeouts, 429, 5xx, network."""

    code = "provider_error"

    def __init__(
        self,
        kind: ProviderErrorKind,
        message: str,
        *,
        provider: str,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.provider = provider
        self.status_code = status_code

    @property
    def retryable(self) -> bool:
        return self.kind in RETRYABLE_KINDS

    def __str__(self) -> str:
        status = f" ({self.status_code})" if self.status_code else ""
        return f"{self.provider} {self.kind.value}{status}: {self.message}"


@dataclass(frozen=True, slots=True)
class Attempt:
    provider: str
    model: str
    outcome: str


USER_MESSAGE = "The model provider is unavailable right now. Please try again in a moment."


class ProviderUnavailableError(SecondMindError):
    """Every option for a step failed (primary, retries, fallback). The message is user-safe;
    ``attempts`` carries the detail for logs and the glass box."""

    code = "provider_unavailable"

    def __init__(self, step: str, attempts: list[Attempt]) -> None:
        super().__init__(USER_MESSAGE)
        self.step = step
        self.attempts = attempts

    @property
    def detail(self) -> str:
        return "; ".join(f"{a.provider}:{a.model} {a.outcome}" for a in self.attempts)


class StreamInterruptedError(ProviderUnavailableError):
    """The provider failed after tokens reached the user; never retried (S1.8)."""

    code = "provider_stream_interrupted"
