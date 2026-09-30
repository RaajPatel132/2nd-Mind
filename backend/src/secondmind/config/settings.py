"""Environment settings: loaded from the environment only, validated at start-up (NFR-9.4).

Every field maps to the upper-cased environment variable of the same name. Every variable is
listed with a comment in ``.env.example``.
"""

import os
from collections.abc import Mapping
from datetime import date
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

# One deployed environment (ADR-0035): what stood in for staging is the local rehearsal stack,
# which runs ENV=production too.
Environment = Literal["development", "test", "production"]
ProviderMode = Literal["auto", "live", "fake"]

# backend/ in a checkout, /app in the image: holds config/ and prompts/.
DEFAULT_RESOURCES_DIR = Path(__file__).resolve().parents[3]
# The example secrets in .env.example and compose: fine locally, refused on shared stacks.
DEFAULT_SESSION_SECRETS = frozenset(
    {
        "change-me-local-dev-secret-0123456789abcdef",
        "test-secret-test-secret-test-secret-000",
    }
)


class Settings(BaseSettings):
    """All process configuration. Construct with :func:`load_settings`."""

    # An empty variable is an unset one: compose passes every optional variable through, empty
    # when it has no value (load_settings drops blanks the same way).
    model_config = SettingsConfigDict(
        case_sensitive=False, extra="ignore", frozen=True, env_ignore_empty=True
    )

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

    # --- spend safety (R.10, ADR-0032): quotas, caps, provider credit, kill switch, rate limit
    quota_usd_guest: Annotated[Decimal, Field(ge=0)] = Decimal("0.75")
    quota_usd_standard: Annotated[Decimal, Field(ge=0)] = Decimal("2.50")
    quota_usd_premium: Annotated[Decimal, Field(ge=0)] = Decimal("4.00")
    spend_cap_daily_usd: Annotated[Decimal, Field(ge=0)] = Decimal("0.50")
    spend_cap_monthly_usd: Annotated[Decimal, Field(ge=0)] = Decimal(5)
    spend_cap_warn_ratio: Annotated[float, Field(gt=0, le=1)] = 0.8
    provider_credit_usd_anthropic: Annotated[Decimal | None, Field(ge=0)] = None
    provider_credit_usd_openai: Annotated[Decimal | None, Field(ge=0)] = None
    provider_credit_since: date | None = None
    kill_switch: bool = False
    rate_turns_per_minute: Annotated[int, Field(ge=1, le=10_000)] = 10

    # --- links (S4.6, S4.7): fetching is safe by construction, and bounded
    link_fetch_timeout_s: Annotated[float, Field(gt=0, le=60)] = 10.0
    link_max_bytes: Annotated[int, Field(ge=10_000, le=20_000_000)] = 2_000_000
    link_max_redirects: Annotated[int, Field(ge=0, le=10)] = 5
    link_chunk_tokens: Annotated[int, Field(ge=50, le=2_000)] = 300
    link_max_chunks: Annotated[int, Field(ge=1, le=200)] = 40
    max_links_per_message: Annotated[int, Field(ge=1, le=20)] = 3
    max_links_per_message_guest: Annotated[int, Field(ge=1, le=20)] = 1
    rate_fetches_per_minute: Annotated[int, Field(ge=1, le=1_000)] = 10
    rate_fetches_per_minute_guest: Annotated[int, Field(ge=1, le=1_000)] = 3
    # Test-only: comma separated host names that may be fetched although they are private, and on
    # any port, so the E2E stack can read its own fixture page server. Refused in production.
    link_allow_private_hosts: str = ""

    # --- web hardening (R.11) and behaviour behind a load balancer (R.12)
    max_request_bytes: Annotated[int, Field(ge=1_024, le=50_000_000)] = 262_144
    # Origins besides the request's own host that may make state-changing requests (comma
    # separated, e.g. "https://2nd-mind.example.com"); empty: same-origin only.
    allowed_origins: str = ""
    # Code sign-in in production, until S6's accounts: DEV_AUTH=true there means "an email plus
    # this code". Open dev sign-in exists only in development and test.
    access_code: SecretStr | None = None
    # Off until S5 opens the site: while off, every way in (code sign-in, guest, persona) asks
    # for the access code.
    guests_open: bool = False
    login_attempts_per_minute: Annotated[int, Field(ge=1, le=1_000)] = 5
    sse_heartbeat_s: Annotated[float, Field(gt=0, le=300)] = 15.0
    shutdown_grace_s: Annotated[float, Field(ge=0, le=600)] = 30.0

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
            self._deployed_guards()
        return self

    def _deployed_guards(self) -> None:
        """Production runs on shared infrastructure with real keys (R.11, ADR-0035)."""
        if self.session_secret.get_secret_value() in DEFAULT_SESSION_SECRETS:
            raise ValueError(f"SESSION_SECRET must not be the example value when ENV={self.env}")
        if not self.session_cookie_secure:
            raise ValueError(f"SESSION_COOKIE_SECURE must be true when ENV={self.env}")
        if self.model_provider_mode != "live":
            raise ValueError(f"MODEL_PROVIDER_MODE must be live when ENV={self.env}")
        if self.tracing_enabled and not self.tracing_configured:
            raise ValueError(
                f"tracing is on but not configured when ENV={self.env}: set LANGFUSE_HOST, "
                "LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY, or TRACING_ENABLED=false"
            )
        if self.dev_auth and self.access_code is None:
            raise ValueError(
                "DEV_AUTH (code sign-in) on production needs ACCESS_CODE: open sign-in exists "
                "only in development"
            )
        if self.log_include_content:
            raise ValueError(f"LOG_INCLUDE_CONTENT must be false when ENV={self.env}")
        if self.link_allow_private_hosts.strip():
            raise ValueError(
                f"LINK_ALLOW_PRIVATE_HOSTS opens the link fetcher to private hosts: it is for "
                f"development and test only, not ENV={self.env}"
            )

    @property
    def allow_private_hosts(self) -> frozenset[str]:
        return frozenset(
            h.strip().lower() for h in self.link_allow_private_hosts.split(",") if h.strip()
        )

    @property
    def access_code_required(self) -> bool:
        """Sign-in asks for the access code: production, while sign-in is by code."""
        return self.dev_auth and self.env == "production"

    @property
    def dev_helpers(self) -> bool:
        """The /v1/dev helpers (seeding, resets) exist where open dev sign-in does."""
        return self.dev_auth and self.env in ("development", "test")

    @property
    def allowed_origin_list(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]

    @property
    def quota_limits_usd(self) -> dict[str, Decimal]:
        return {
            "guest": self.quota_usd_guest,
            "standard": self.quota_usd_standard,
            "premium": self.quota_usd_premium,
        }

    @property
    def provider_credits_usd(self) -> dict[str, Decimal]:
        """What the app may spend per provider (only the providers with a limit set)."""
        limits = {
            "anthropic": self.provider_credit_usd_anthropic,
            "openai": self.provider_credit_usd_openai,
        }
        return {k: v for k, v in limits.items() if v is not None}

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
