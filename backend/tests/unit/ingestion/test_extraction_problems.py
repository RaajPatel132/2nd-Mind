"""An extraction that needed a retry says why in the log (R.3), without the model's own values."""

from secondmind.ingestion.pipeline import _problems


def test_problems_are_logged_without_the_models_own_values() -> None:
    """A retry's reasons go to the log; the person's words (which a slug or a state can echo)
    stay out of it."""
    errors = [
        "memory m1: state 'hypothetical' is not one of ['wanted', 'active']",
        "memory m2: subtype 'Gift for Mom' is not a slug",
        "memory m1: state 'hypothetical' is not one of ['wanted', 'active']",
    ]
    problems = _problems(errors)
    assert problems == [
        "memory m1: state '…' is not one of ['…', '…']",
        "memory m2: subtype '…' is not a slug",
    ]
    assert not any("Mom" in p for p in problems)
