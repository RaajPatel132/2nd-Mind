"""R.13: docs/deploy/config.md lists every variable the application reads, with where its value
comes from. A new Settings field without a row fails here (as one without a .env.example line
fails there), and anything that carries a secret has to be sourced from Secrets Manager."""

import re
from pathlib import Path

from pydantic import SecretStr

from secondmind.config import Settings

MATRIX = Path(__file__).parents[3] / "docs" / "deploy" / "config.md"
SOURCES = {"env", "ssm", "secret"}
# Not SecretStr, but they carry a credential: a password inside a URL.
SECRET_BY_NATURE = {"DATABASE_URL", "REDIS_URL", "DATABASE_MIGRATION_URL"}


def rows() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for line in MATRIX.read_text().splitlines():
        match = re.match(r"^\|\s*`([^`]+)`\s*\|(.*)\|\s*$", line)
        if match:
            out[match.group(1)] = [c.strip() for c in match.group(2).split("|")]
    return out


def test_every_setting_has_a_row() -> None:
    documented = set(rows())
    missing = {name.upper() for name in Settings.model_fields} - documented
    assert not missing, f"add {sorted(missing)} to docs/deploy/config.md"


def test_every_row_says_where_the_value_comes_from() -> None:
    for name, cells in rows().items():
        assert len(cells) == 5, f"{name}: expected meaning, three environments and a source"
        assert cells[-1] in SOURCES, f"{name}: source {cells[-1]!r} is not one of {SOURCES}"
        assert all(cells[:-1]), f"{name}: an empty cell"


def test_secrets_come_from_secrets_manager() -> None:
    table = rows()
    secret_fields = {
        name.upper()
        for name, field in Settings.model_fields.items()
        if SecretStr in _members(field.annotation)
    }
    for name in secret_fields | SECRET_BY_NATURE:
        assert name in table, name
        assert table[name][-1] == "secret", f"{name} holds a secret: source must be `secret`"


def test_no_row_carries_a_real_looking_secret() -> None:
    text = MATRIX.read_text()
    assert not re.search(r"sk-[A-Za-z0-9_-]{16,}|pk-lf-[A-Za-z0-9]{8,}", text)


def _members(annotation: object) -> tuple[object, ...]:
    args = getattr(annotation, "__args__", None)
    return tuple(args) if args else (annotation,)
