"""S1.1: one top-level module per domain, each documented with a one-line docstring."""

import importlib

import pytest

MODULES = [
    "api",
    "agent",
    "ingestion",
    "retrieval",
    "corrections",
    "memory",
    "providers",
    "policy",
    "metering",
    "auth",
    "jobs",
    "observability",
    "evals",
    "config",
    "core",
]


@pytest.mark.parametrize("name", MODULES)
def test_module_exists_with_one_line_docstring(name: str) -> None:
    module = importlib.import_module(f"secondmind.{name}")
    doc = (module.__doc__ or "").strip()
    assert doc, f"secondmind.{name} needs a docstring saying what belongs there"
    assert "\n" not in doc.splitlines()[0]
