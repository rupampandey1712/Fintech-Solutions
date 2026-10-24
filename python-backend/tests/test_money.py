import pytest

from app.domain.money import Money


def test_add_same_currency():
    assert Money(100, "USD").add(Money(250, "USD")) == Money(350, "USD")


def test_subtract_same_currency():
    assert Money(500, "EUR").subtract(Money(120, "EUR")) == Money(380, "EUR")


def test_mixed_currency_rejected():
    with pytest.raises(ValueError):
        Money(100, "USD").add(Money(100, "EUR"))


def test_non_integer_amount_rejected():
    with pytest.raises(TypeError):
        Money(10.5, "USD")


def test_invalid_currency_rejected():
    with pytest.raises(ValueError):
        Money(10, "usd")
    with pytest.raises(ValueError):
        Money(10, "USDX")
