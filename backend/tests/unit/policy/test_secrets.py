"""S2.3 / ADR-0021: the deterministic secret pre-check finds and redacts secret-shaped spans.
Every value below is synthetic."""

import pytest

from secondmind.policy import REDACTED, redact_values, scan_secrets

# Key-shaped values are assembled at runtime so secret scanners don't flag this test file.
OPENAI_LIKE = "sk-" + "proj-" + "AbCdEfGhIjKlMnOpQrStUvWx12"
AWS_LIKE = "AKIA" + "ABCDEFGHIJKLMNOP"
GITHUB_LIKE = "ghp" + "_" + "abcdefghijklmnopqrstuvwxyz0123456789"
GENERIC_LIKE = "abcDEF" + "1234567890xyz"


@pytest.mark.parametrize(
    ("text", "secret", "kind"),
    [
        ("Remember my wifi password is hunter2", "hunter2", "password"),
        ("the password for the router: Tr0ub4dor&3", "Tr0ub4dor&3", "password"),
        ("My bank PIN is 4821", "4821", "pin"),
        ("pin 90210 for the gym locker", "90210", "pin"),
        ("OTP 551203, use it quickly", "551203", "one_time_code"),
        ("one-time code is 889913", "889913", "one_time_code"),
        (f"api key = {GENERIC_LIKE}", GENERIC_LIKE, "api_key"),
        (f"use {OPENAI_LIKE} for the script", OPENAI_LIKE, "api_key"),
        (f"aws key {AWS_LIKE}", AWS_LIKE, "api_key"),
        (f"token {GITHUB_LIKE}", GITHUB_LIKE, "api_key"),
    ],
)  # fmt: skip
def test_secret_shapes_are_found_and_redacted(text: str, secret: str, kind: str) -> None:
    scan = scan_secrets(text)
    assert scan.found
    assert kind in scan.kinds
    assert secret not in scan.redacted
    assert REDACTED in scan.redacted


@pytest.mark.parametrize(
    "text",
    [
        "I'm vegetarian",
        "my password is the same as before",
        "pin the tweet about the meetup",
        "Kabir's birthday is 14 March",
        "Ran 5 km in 31 min",
        "the code review is on Friday",
    ],
)
def test_ordinary_messages_are_not_secrets(text: str) -> None:
    scan = scan_secrets(text)
    assert not scan.found
    assert scan.redacted == text


def test_context_around_the_secret_is_kept() -> None:
    scan = scan_secrets("Remember my wifi password is hunter2, thanks")
    assert scan.redacted == f"Remember my wifi password is {REDACTED}, thanks"


def test_model_labelled_values_are_redacted_literally() -> None:
    assert redact_values("door code 7731 and gate 7731", ["7731"]) == (
        f"door code {REDACTED} and gate {REDACTED}"
    )
