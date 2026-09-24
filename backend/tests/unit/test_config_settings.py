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


def test_empty_values_count_as_unset(base_env: dict[str, str]) -> None:
    base_env["ANTHROPIC_API_KEY"] = "   "
    assert load_settings(base_env).anthropic_api_key is None


def test_env_example_lists_every_setting() -> None:
    example = (Path(__file__).parents[3] / ".env.example").read_text()
    names = {line.split("=", 1)[0].lstrip("# ").strip() for line in example.splitlines()}
    missing = [f.upper() for f in Settings.model_fields if f.upper() not in names]
    assert not missing, f".env.example is missing: {missing}"
