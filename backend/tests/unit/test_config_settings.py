"""S1.4: settings come from the environment only and fail start-up with a clear message."""

from pathlib import Path

import pytest

from secondmind.config import Settings, load_settings
from secondmind.core import ConfigError


def test_valid_environment_loads(base_env: dict[str, str]) -> None:
    settings = load_settings(base_env)
    assert settings.env == "test"
    assert settings.database_url.scheme == "postgresql+asyncpg"
    assert settings.dev_auth is False


def test_missing_required_variable_names_it(base_env: dict[str, str]) -> None:
    del base_env["DATABASE_URL"]
    with pytest.raises(ConfigError) as exc:
        load_settings(base_env)
    assert "DATABASE_URL" in exc.value.message
    assert "Field required" in exc.value.message


def test_invalid_values_name_every_variable(base_env: dict[str, str]) -> None:
    base_env |= {"PROVIDER_MAX_RETRIES": "99", "DEFAULT_TIMEZONE": "Mars/Olympus"}
    with pytest.raises(ConfigError) as exc:
        load_settings(base_env)
    assert "PROVIDER_MAX_RETRIES" in exc.value.message
    assert "DEFAULT_TIMEZONE" in exc.value.message
    assert "Mars/Olympus" in exc.value.message


def test_short_session_secret_is_rejected(base_env: dict[str, str]) -> None:
    base_env["SESSION_SECRET"] = "too-short"
    with pytest.raises(ConfigError, match="SESSION_SECRET"):
        load_settings(base_env)


def test_staging_is_retired(base_env: dict[str, str]) -> None:
    with pytest.raises(ConfigError, match="ENV"):
        load_settings(base_env | {"ENV": "staging"})


SECURE_SECRET = "a-long-random-staging-secret-value-0123456789"


def deployed(base_env: dict[str, str], env: str = "production", **changes: str) -> dict[str, str]:
    """A configuration that satisfies every start-up guard for ``env``, changed as asked."""
    return (
        base_env
        | {
            "ENV": env,
            "SESSION_SECRET": SECURE_SECRET,
            "SESSION_COOKIE_SECURE": "true",
            "MODEL_PROVIDER_MODE": "live",
            "ANTHROPIC_API_KEY": "sk-ant-not-a-real-key-for-the-guard-test",
            "OPENAI_API_KEY": "sk-not-a-real-key-for-the-guard-test-00",
            "TRACING_ENABLED": "false",
        }
        | changes
    )


def test_a_production_configuration_that_meets_every_guard_loads(base_env: dict[str, str]) -> None:
    assert load_settings(deployed(base_env)).env == "production"


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (
            {"SESSION_SECRET": "test-secret-test-secret-test-secret-000"},
            "SESSION_SECRET must not be the example value",
        ),
        (
            {"SESSION_SECRET": "change-me-local-dev-secret-0123456789abcdef"},
            "SESSION_SECRET must not be the example value",
        ),
        ({"SESSION_COOKIE_SECURE": "false"}, "SESSION_COOKIE_SECURE must be true"),
        ({"MODEL_PROVIDER_MODE": "auto"}, "MODEL_PROVIDER_MODE must be live"),
        ({"MODEL_PROVIDER_MODE": "fake"}, "MODEL_PROVIDER_MODE must be live"),
        ({"TRACING_ENABLED": "true"}, "tracing is on but not configured"),
        ({"LOG_INCLUDE_CONTENT": "true"}, "LOG_INCLUDE_CONTENT must be false"),
    ],
)
def test_each_deployed_guard_refuses_start_up(
    base_env: dict[str, str], change: dict[str, str], message: str
) -> None:
    with pytest.raises(ConfigError, match=message):
        load_settings(deployed(base_env, **change))


def test_tracing_may_be_on_when_it_is_configured_or_off_on_purpose(
    base_env: dict[str, str],
) -> None:
    configured = {
        "TRACING_ENABLED": "true",
        "LANGFUSE_HOST": "https://cloud.langfuse.example",
        "LANGFUSE_PUBLIC_KEY": "pk-lf-not-real",
        "LANGFUSE_SECRET_KEY": "sk-lf-not-real",
    }
    assert load_settings(deployed(base_env, **configured)).tracing_configured
    assert not load_settings(deployed(base_env, TRACING_ENABLED="false")).tracing_configured


def test_content_logging_is_refused_in_production(base_env: dict[str, str]) -> None:
    with pytest.raises(ConfigError, match="LOG_INCLUDE_CONTENT"):
        load_settings(deployed(base_env, LOG_INCLUDE_CONTENT="true"))


def test_code_sign_in_on_production_needs_the_access_code(base_env: dict[str, str]) -> None:
    # DEV_AUTH in production is code sign-in, and it needs the code (ADR-0034 as amended).
    with pytest.raises(ConfigError, match="needs ACCESS_CODE"):
        load_settings(deployed(base_env, DEV_AUTH="true"))
    ok = load_settings(deployed(base_env, DEV_AUTH="true", ACCESS_CODE="open-sesame-42"))
    assert ok.access_code is not None
    assert ok.access_code.get_secret_value() == "open-sesame-42"
    assert ok.access_code_required
    assert not ok.dev_helpers  # the /v1/dev helpers are for development only


def test_open_dev_sign_in_and_its_helpers_exist_only_in_development(
    base_env: dict[str, str],
) -> None:
    dev = load_settings(base_env | {"DEV_AUTH": "true"})
    assert dev.dev_helpers
    assert not dev.access_code_required
    assert not load_settings(base_env | {"DEV_AUTH": "false"}).dev_helpers


def test_the_link_fetcher_opens_to_private_hosts_only_in_development_and_test(
    base_env: dict[str, str],
) -> None:
    # The E2E stack reads its own fixture pages through this allowance; a deployed app never may.
    with pytest.raises(ConfigError, match="LINK_ALLOW_PRIVATE_HOSTS"):
        load_settings(deployed(base_env, LINK_ALLOW_PRIVATE_HOSTS="fixtures"))
    for env in ("development", "test"):
        allowed = load_settings(base_env | {"ENV": env, "LINK_ALLOW_PRIVATE_HOSTS": "Fixtures, b"})
        assert allowed.allow_private_hosts == frozenset({"fixtures", "b"})
    assert load_settings(base_env).allow_private_hosts == frozenset()


def test_guests_are_closed_until_s5_opens_the_site(base_env: dict[str, str]) -> None:
    assert not load_settings(base_env).guests_open
    assert load_settings(base_env | {"GUESTS_OPEN": "true"}).guests_open


def test_local_and_test_environments_have_no_deployed_guards(base_env: dict[str, str]) -> None:
    # The defaults a developer starts with still work: the guards are for shared infrastructure.
    for env in ("development", "test"):
        assert load_settings(base_env | {"ENV": env}).env == env


def test_empty_values_count_as_unset(base_env: dict[str, str]) -> None:
    base_env["ANTHROPIC_API_KEY"] = "   "
    assert load_settings(base_env).anthropic_api_key is None


def test_env_example_lists_every_setting() -> None:
    example = (Path(__file__).parents[3] / ".env.example").read_text()
    names = {line.split("=", 1)[0].lstrip("# ").strip() for line in example.splitlines()}
    missing = [f.upper() for f in Settings.model_fields if f.upper() not in names]
    assert not missing, f".env.example is missing: {missing}"


def test_an_empty_environment_variable_is_an_unset_one(
    base_env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # Compose passes optional variables through empty; Settings must read the process
    # environment the way load_settings reads a mapping.
    for name in ("LANGFUSE_HOST", "PROVIDER_CREDIT_USD_ANTHROPIC", "PROVIDER_CREDIT_SINCE"):
        monkeypatch.setenv(name, "")
    from secondmind.config import Settings  # noqa: PLC0415

    for name, value in base_env.items():
        monkeypatch.setenv(name, value)
    settings = Settings()  # type: ignore[call-arg]
    assert settings.langfuse_host is None
    assert settings.provider_credit_usd_anthropic is None
    assert settings.provider_credit_since is None
