"""Immutable journal drafts and double-entry balance validation."""

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from app.domain.money import Money


class EntrySide(StrEnum):
    DEBIT = "debit"
    CREDIT = "credit"


@dataclass(frozen=True, slots=True)
class JournalLineDraft:
    ledger_account_id: UUID
    side: EntrySide
    amount: Money

    def __post_init__(self) -> None:
        if self.amount.amount_minor <= 0:
            raise ValueError("Journal line amounts must be positive")


@dataclass(frozen=True, slots=True)
class JournalDraft:
    business_reference: str
    currency: str
    lines: tuple[JournalLineDraft, ...]

    def __post_init__(self) -> None:
        if not self.business_reference.strip():
            raise ValueError("A journal requires a business reference")
        if len(self.lines) < 2:
            raise ValueError("A posted journal requires at least two lines")
        if any(line.amount.currency != self.currency for line in self.lines):
            raise ValueError("All journal lines must use the journal currency")
        debits = sum(
            line.amount.amount_minor for line in self.lines if line.side is EntrySide.DEBIT
        )
        credits = sum(
            line.amount.amount_minor for line in self.lines if line.side is EntrySide.CREDIT
        )
        if debits != credits:
            raise ValueError(f"Journal is unbalanced: debit={debits}, credit={credits}")
