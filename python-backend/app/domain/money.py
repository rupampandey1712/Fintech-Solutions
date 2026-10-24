"""Currency-aware money represented only in integer minor units."""

import re
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Money:
    amount_minor: int
    currency: str

    def __post_init__(self) -> None:
        if isinstance(self.amount_minor, bool) or not isinstance(self.amount_minor, int):
            raise TypeError("amount_minor must be an integer")
        if not re.fullmatch(r"[A-Z]{3}", self.currency):
            raise ValueError("currency must be a three-letter uppercase ISO code")

    def add(self, other: "Money") -> "Money":
        self._require_same_currency(other)
        return Money(self.amount_minor + other.amount_minor, self.currency)

    def subtract(self, other: "Money") -> "Money":
        self._require_same_currency(other)
        return Money(self.amount_minor - other.amount_minor, self.currency)

    def _require_same_currency(self, other: "Money") -> None:
        if self.currency != other.currency:
            raise ValueError("Money values with different currencies cannot be combined")
