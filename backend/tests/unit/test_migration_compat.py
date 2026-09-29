"""One-release compatibility (NFR-10.4, R.12): during a rolling deploy the old code runs against
the new schema for a while. A migration that drops or renames a column or table therefore must
say which earlier migration's release already stopped using it (``CONTRACT_AFTER``), so the
change is always two releases, never one."""

import ast
from pathlib import Path

import pytest

VERSIONS = Path(__file__).resolve().parents[2] / "migrations" / "versions"
DESTRUCTIVE_CALLS = {"drop_column", "drop_table", "rename_table"}
DESTRUCTIVE_SQL = ("DROP COLUMN", "DROP TABLE", "RENAME COLUMN", "RENAME TO")


def _upgrade_of(tree: ast.Module) -> ast.FunctionDef | None:
    return next(
        (n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "upgrade"), None
    )


def destructive_operations(source: str) -> list[str]:
    """What the ``upgrade()`` of a migration removes or renames (the downgrade may drop freely)."""
    upgrade = _upgrade_of(ast.parse(source))
    if upgrade is None:
        return []
    found: list[str] = []
    for node in ast.walk(upgrade):
        if isinstance(node, ast.Call):
            name = node.func.attr if isinstance(node.func, ast.Attribute) else ""
            if name in DESTRUCTIVE_CALLS:
                found.append(name)
            if name == "alter_column" and any(k.arg == "new_column_name" for k in node.keywords):
                found.append("rename column")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            found.extend(s for s in DESTRUCTIVE_SQL if s in node.value.upper())
    return found


def _contract_after(tree: ast.Module) -> str | None:
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "CONTRACT_AFTER" for t in node.targets
        ):
            return str(ast.literal_eval(node.value))
    return None


def test_a_migration_that_drops_or_renames_names_the_release_that_stopped_using_it() -> None:
    for path in sorted(VERSIONS.glob("[0-9]*.py")):
        source = path.read_text(encoding="utf-8")
        ops = destructive_operations(source)
        if not ops:
            continue
        earlier = _contract_after(ast.parse(source))
        assert earlier, f"{path.name} does {ops} without CONTRACT_AFTER (see CONTRIBUTING.md)"
        assert earlier < path.name[:4], f"{path.name}: CONTRACT_AFTER must name an earlier revision"
        assert any(VERSIONS.glob(f"{earlier}_*.py")), f"{path.name}: no revision {earlier}"


ADD_ONLY = "def upgrade():\n    op.add_column('t', 'c')\ndef downgrade():\n    op.drop_column('t')"


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("def upgrade():\n    op.drop_column('t', 'c')\n", ["drop_column"]),
        ("def upgrade():\n    op.alter_column('t', 'c', new_column_name='d')\n", ["rename column"]),
        ("def upgrade():\n    op.execute('ALTER TABLE t DROP COLUMN c')\n", ["DROP COLUMN"]),
        (
            ADD_ONLY,
            [],
        ),
    ],
)
def test_the_check_sees_drops_and_renames_in_upgrade_only(source: str, expected: list[str]) -> None:
    assert destructive_operations(source) == expected
