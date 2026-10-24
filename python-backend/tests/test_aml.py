from app.domain.aml import RAPID_MOVEMENT, STRUCTURING, UNUSUAL_AMOUNT, evaluate_aml


def test_structuring_triggers_at_three_payments():
    assert STRUCTURING in evaluate_aml({"payments_last_hour": 3, "amount_minor": 100})
    assert STRUCTURING not in evaluate_aml({"payments_last_hour": 2, "amount_minor": 100})


def test_rapid_movement_triggers_at_five_payments():
    assert RAPID_MOVEMENT in evaluate_aml({"payments_last_10_min": 5, "amount_minor": 100})
    assert RAPID_MOVEMENT not in evaluate_aml({"payments_last_10_min": 4, "amount_minor": 100})


def test_unusual_amount():
    assert UNUSUAL_AMOUNT in evaluate_aml({"amount_minor": 5_000_001})
    assert UNUSUAL_AMOUNT not in evaluate_aml({"amount_minor": 5_000_000})


def test_custom_threshold():
    assert UNUSUAL_AMOUNT in evaluate_aml({"amount_minor": 1_000}, unusual_amount_threshold=999)


def test_multiple_rules():
    codes = evaluate_aml({
        "payments_last_hour": 5, "payments_last_10_min": 6, "amount_minor": 9_000_000,
    })
    assert codes == [STRUCTURING, RAPID_MOVEMENT, UNUSUAL_AMOUNT]
