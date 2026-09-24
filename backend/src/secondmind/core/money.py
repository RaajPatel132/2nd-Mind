"""USD amounts: exact Decimal in code, a JSON number on the wire."""

from decimal import Decimal
from typing import Annotated

from pydantic import PlainSerializer

USD_QUANTUM = Decimal("0.00000001")

UsdAmount = Annotated[
    Decimal,
    PlainSerializer(float, return_type=float, when_used="json"),
]


def usd(value: Decimal | int | str) -> Decimal:
    """Normalise to 8 decimal places (sub-cent precision for per-call costs)."""
    return Decimal(value).quantize(USD_QUANTUM)
