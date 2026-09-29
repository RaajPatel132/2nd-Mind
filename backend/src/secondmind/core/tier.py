"""Who may spend what (ADR-0032): the tier decides a person's quota and the models they can pick."""

from enum import StrEnum


class Tier(StrEnum):
    GUEST = "guest"
    STANDARD = "standard"
    PREMIUM = "premium"
