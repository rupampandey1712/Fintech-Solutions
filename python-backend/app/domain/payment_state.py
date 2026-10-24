"""Payment lifecycle state machine shared conceptually with the .NET domain."""

from enum import StrEnum


class PaymentStatus(StrEnum):
    INITIATED = "initiated"
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    EXPIRED = "expired"
    REVERSED = "reversed"


_ALLOWED_TRANSITIONS: dict[PaymentStatus, frozenset[PaymentStatus]] = {
    PaymentStatus.INITIATED: frozenset({
        PaymentStatus.PENDING, PaymentStatus.REJECTED, PaymentStatus.CANCELLED,
    }),
    # PENDING -> REJECTED: a payment held for fraud review is refused by the reviewer.
    PaymentStatus.PENDING: frozenset({
        PaymentStatus.PROCESSING, PaymentStatus.CANCELLED, PaymentStatus.EXPIRED,
        PaymentStatus.REJECTED,
    }),
    PaymentStatus.PROCESSING: frozenset({PaymentStatus.COMPLETED, PaymentStatus.FAILED}),
    # A completed payment posted in error is corrected by reversing its journal entries.
    PaymentStatus.COMPLETED: frozenset({PaymentStatus.REVERSED}),
}


def transition(current: PaymentStatus, target: PaymentStatus) -> PaymentStatus:
    if target not in _ALLOWED_TRANSITIONS.get(current, frozenset()):
        raise ValueError(f"Payment transition {current.value} -> {target.value} is not allowed")
    return target
