"""The note a visitor reads when the sample opens names a person, not a seed id."""

import pytest

from secondmind.persona.service import display_name


@pytest.mark.parametrize(
    ("seed_id", "shown"),
    [("aditi-rao", "Aditi Rao"), ("kabir", "Kabir"), ("mary-jane-watson", "Mary Jane Watson")],
)
def test_a_seed_id_reads_as_a_name(seed_id: str, shown: str) -> None:
    assert display_name(seed_id) == shown
