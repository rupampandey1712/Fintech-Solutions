from uuid import uuid4

import pytest

from app.domain.ledger import EntrySide, JournalDraft, JournalLineDraft
from app.domain.money import Money


def test_balanced_journal_draft():
    debit = JournalLineDraft(uuid4(), EntrySide.DEBIT, Money(500, "USD"))
    credit = JournalLineDraft(uuid4(), EntrySide.CREDIT, Money(500, "USD"))
    draft = JournalDraft("payment:1", "USD", (debit, credit))
    assert sum(line.amount.amount_minor for line in draft.lines) == 1000


def test_unbalanced_journal_raises():
    debit = JournalLineDraft(uuid4(), EntrySide.DEBIT, Money(500, "USD"))
    credit = JournalLineDraft(uuid4(), EntrySide.CREDIT, Money(499, "USD"))
    with pytest.raises(ValueError):
        JournalDraft("payment:1", "USD", (debit, credit))


def test_single_line_journal_raises():
    line = JournalLineDraft(uuid4(), EntrySide.DEBIT, Money(500, "USD"))
    with pytest.raises(ValueError):
        JournalDraft("payment:1", "USD", (line,))


def test_currency_mismatch_raises():
    debit = JournalLineDraft(uuid4(), EntrySide.DEBIT, Money(500, "USD"))
    credit = JournalLineDraft(uuid4(), EntrySide.CREDIT, Money(500, "EUR"))
    with pytest.raises(ValueError):
        JournalDraft("payment:1", "USD", (debit, credit))


def test_empty_reference_raises():
    debit = JournalLineDraft(uuid4(), EntrySide.DEBIT, Money(500, "USD"))
    credit = JournalLineDraft(uuid4(), EntrySide.CREDIT, Money(500, "USD"))
    with pytest.raises(ValueError):
        JournalDraft("   ", "USD", (debit, credit))
