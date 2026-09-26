"""Environment settings: loaded from the environment only, validated at start-up (NFR-9.4).

Every field maps to the upper-cased environment variable of the same name. Every variable is
listed with a comment in ``.env.example``.
"""

import os
from collections.abc import Mapping
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Literal, Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    AnyHttpUrl,
    Field,
    PostgresDsn,
    RedisDsn,
    SecretStr,
    ValidationError,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

from secondmind.core import ConfigError

Environment = Literal["development", "test", "staging", "production"]
ProviderMode = Literal["auto", "live", "fake"]

# backend/ in a checkout, /app in the image: holds config/ and prompts/.
DEFAULT_RESOURCES_DIR = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    """All process configuration. Construct with :func:`load_settings`."""

    model_config = SettingsConfigDict(case_sensitive=False, extra="ignore", frozen=True)

    # --- runtime
    env: Environment = "development"
    app_version: str = "dev"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_format: Literal["json", "console"] = "json"
    log_include_content: bool = False
    resources_dir: Path = DEFAULT_RESOURCES_DIR

    # --- stores
    database_url: PostgresDsn
    database_pool_size: Annotated[int, Field(ge=1, le=100)] = 10
    redis_url: RedisDsn

    # --- model providers
    model_provider_mode: ProviderMode = "auto"
    anthropic_api_key: SecretStr | None = None
    openai_api_key: SecretStr | None = None
    provider_max_retries: Annotated[int, Field(ge=0, le=5)] = 2
    provider_retry_base_ms: Annotated[int, Field(ge=1, le=10_000)] = 250
    provider_retry_max_ms: Annotated[int, Field(ge=1, le=60_000)] = 4_000
    breaker_failure_threshold: Annotated[int, Field(ge=1, le=100)] = 5
    breaker_window_s: Annotated[float, Field(gt=0, le=3_600)] = 60.0
    breaker_cooldown_s: Annotated[float, Field(gt=0, le=3_600)] = 30.0
    fake_provider_token_delay_ms: Annotated[int, Field(ge=0, le=1_000)] = 15

    # --- tracing via Langfuse
    tracing_enabled: bool = True
    langfuse_host: AnyHttpUrl | None = None
    langfuse_public_key: str | None = None
    langfuse_secret_key: SecretStr | None = None
    langfuse_ui_url: AnyHttpUrl | None = None
    langfuse_project_id: str | None = None
    trace_include_content: bool = False

    # --- auth
    dev_auth: bool = False
    dev_user_email: str = "dev@example.com"
    session_secret: SecretStr = Field(min_length=32)
    session_cookie_secure: bool = False

    # --- product limits
    max_message_chars: Annotated[int, Field(ge=1, le=100_000)] = 8_000
    default_timezone: str = "UTC"

    # --- memory: write policy, layers, reconciliation, keys (S2)
    policy_bulk_threshold: Annotated[int, Field(ge=1, le=1_000)] = 5
    core_token_budget: Annotated[int, Field(ge=100, le=50_000)] = 1_500
    quick_horizon_days: Annotated[int, Field(ge=1, le=365)] = 30
    quick_recent_days: Annotated[int, Field(ge=0, le=90)] = 7
    reconcile_similarity_threshold: Annotated[float, Field(gt=0, le=1)] = 0.85
    cue_keys_max: Annotated[int, Field(ge=0, le=10)] = 3
    embed_dimensions: Annotated[int, Field(ge=8, le=4_096)] = 1_536
    enrich_enabled: bool = True
    verbal_keys_enabled: bool = True
    soft_channel_enabled: bool = True

    # --- recall: channels, fusion, rerank, selection, triggers (S3)
    soft_channel_k: Annotated[int, Field(ge=1, le=200)] = 20
    rrf_k: Annotated[int, Field(ge=1, le=1_000)] = 60
    history_demotion: Annotated[float, Field(ge=0, le=1)] = 0.5
    rerank_enabled: bool = True
    rerank_top_n: Annotated[int, Field(ge=1, le=100)] = 20
    rerank_min_score: Annotated[float, Field(ge=0, le=1)] = 0.5
    answer_top_k: Annotated[int, Field(ge=1, le=50)] = 8
    list_max_items: Annotated[int, Field(ge=1, le=200)] = 20
    tool_timeout_ms: Annotated[int, Field(ge=50, le=60_000)] = 3_000
    count_check_min_score: Annotated[float, Field(ge=0, le=1)] = 0.6
    trigger_similarity_threshold: Annotated[float, Field(gt=0, le=1)] = 0.6
    upcoming_days: Annotated[int, Field(ge=1, le=365)] = 30
    quick_frequent_min: Annotated[int, Field(ge=1, le=100)] = 3

    # --- reserved for S4: quotas, spend caps and the kill switch
    quota_tokens_guest: Annotated[int, Field(ge=0)] = 50_000
    quota_tokens_standard: Annotated[int, Field(ge=0)] = 1_000_000
    quota_tokens_premium: Annotated[int, Field(ge=0)] = 10_000_000
    spend_cap_daily_usd: Annotated[Decimal, Field(ge=0)] = Decimal(5)
    spend_cap_monthly_usd: Annotated[Decimal, Field(ge=0)] = Decimal(50)
    kill_switch: bool = False

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # Environment only: no .env files, no secrets directory.
        return (init_settings, env_settings)

    @field_validator("default_timezone")
    @classmethod
    def _valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown IANA timezone {value!r}") from exc
        return value

    @model_validator(mode="after")
    def _production_guards(self) -> Self:
        if self.env == "production":
            if self.dev_auth:
                raise ValueError("DEV_AUTH must be false when ENV=production")
            if self.log_include_content:
                raise ValueError("LOG_INCLUDE_CONTENT must be false when ENV=production")
            if self.model_provider_mode == "fake":
                raise ValueError("MODEL_PROVIDER_MODE=fake is not allowed when ENV=production")
        return self

    @property
    def tracing_configured(self) -> bool:
        return bool(
            self.tracing_enabled
            and self.langfuse_host
            and self.langfuse_public_key
            and self.langfuse_secret_key
        )


def load_settings(environ: Mapping[str, str] | None = None) -> Settings:
    """Validate settings from ``environ`` (default: the process environment).

    Raises :class:`ConfigError` whose message names every offending variable.
    """
    source = os.environ if environ is None else environ
    fields = Settings.model_fields
    values = {
        key.lower(): value
        for key, value in source.items()
        if key.lower() in fields and value.strip() != ""
    }
    try:
        return Settings.model_validate(values)
    except ValidationError as exc:
        raise ConfigError(format_validation_error(exc)) from exc


def format_validation_error(exc: ValidationError) -> str:
    lines = ["Invalid configuration:"]
    for err in exc.errors():
        loc = err.get("loc", ())
        name = str(loc[0]).upper() if loc else "(settings)"
        msg = err.get("msg", "invalid value")
        if msg.startswith("Value error, "):
            msg = msg.removeprefix("Value error, ")
        lines.append(f"  {name}: {msg}")
    return "\n".join(lines)
