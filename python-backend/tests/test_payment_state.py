import pytest

from app.domain.payment_state import PaymentStatus, transition


def test_valid_transitions():
    assert transition(PaymentStatus.INITIATED, PaymentStatus.PENDING) is PaymentStatus.PENDING
    assert transition(PaymentStatus.PENDING, PaymentStatus.PROCESSING) is PaymentStatus.PROCESSING
    assert transition(PaymentStatus.PROCESSING, PaymentStatus.COMPLETED) is PaymentStatus.COMPLETED
    assert transition(PaymentStatus.PROCESSING, PaymentStatus.FAILED) is PaymentStatus.FAILED
    assert transition(PaymentStatus.PENDING, PaymentStatus.CANCELLED) is PaymentStatus.CANCELLED
    assert transition(PaymentStatus.PENDING, PaymentStatus.EXPIRED) is PaymentStatus.EXPIRED
    assert transition(PaymentStatus.INITIATED, PaymentStatus.REJECTED) is PaymentStatus.REJECTED
    assert transition(PaymentStatus.PENDING, PaymentStatus.REJECTED) is PaymentStatus.REJECTED
    assert transition(PaymentStatus.COMPLETED, PaymentStatus.REVERSED) is PaymentStatus.REVERSED


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (PaymentStatus.INITIATED, PaymentStatus.COMPLETED),
        (PaymentStatus.PENDING, PaymentStatus.COMPLETED),
        (PaymentStatus.COMPLETED, PaymentStatus.FAILED),
        (PaymentStatus.REVERSED, PaymentStatus.COMPLETED),
        (PaymentStatus.FAILED, PaymentStatus.PENDING),
        (PaymentStatus.CANCELLED, PaymentStatus.PROCESSING),
    ],
)
def test_invalid_transitions(current, target):
    with pytest.raises(ValueError):
        transition(current, target)
