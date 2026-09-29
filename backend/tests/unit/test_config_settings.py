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


def test_dev_auth_is_refused_in_production(base_env: dict[str, str]) -> None:
    base_env |= {"ENV": "production", "DEV_AUTH": "true"}
    with pytest.raises(ConfigError, match="DEV_AUTH must be false when ENV=production"):
        load_settings(base_env)


def test_content_logging_is_refused_in_production(base_env: dict[str, str]) -> None:
    base_env |= {"ENV": "production", "LOG_INCLUDE_CONTENT": "true"}
    with pytest.raises(ConfigError, match="LOG_INCLUDE_CONTENT"):
        load_settings(base_env)


SECURE_SECRET = "a-long-random-staging-secret-value-0123456789"


def deployed(base_env: dict[str, str], env: str = "staging", **changes: str) -> dict[str, str]:
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


def test_a_staging_configuration_that_meets_every_guard_loads(base_env: dict[str, str]) -> None:
    assert load_settings(deployed(base_env)).env == "staging"
    assert load_settings(deployed(base_env, "production")).env == "production"


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
@pytest.mark.parametrize("env", ["staging", "production"])
def test_each_deployed_guard_refuses_start_up(
    base_env: dict[str, str], env: str, change: dict[str, str], message: str
) -> None:
    with pytest.raises(ConfigError, match=message):
        load_settings(deployed(base_env, env, **change))


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


def test_dev_login_on_staging_needs_the_access_code(base_env: dict[str, str]) -> None:
    with pytest.raises(ConfigError, match="STAGING_ACCESS_CODE"):
        load_settings(deployed(base_env, DEV_AUTH="true"))
    ok = load_settings(deployed(base_env, DEV_AUTH="true", STAGING_ACCESS_CODE="open-sesame-42"))
    assert ok.staging_access_code is not None
    assert ok.staging_access_code.get_secret_value() == "open-sesame-42"


def test_production_still_refuses_dev_auth_even_with_an_access_code(
    base_env: dict[str, str],
) -> None:
    changes = {"DEV_AUTH": "true", "STAGING_ACCESS_CODE": "open-sesame-42"}
    with pytest.raises(ConfigError, match="DEV_AUTH must be false when ENV=production"):
        load_settings(deployed(base_env, "production", **changes))


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
