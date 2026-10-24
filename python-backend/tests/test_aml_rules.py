"""AML rules: structuring, rapid movement, unusual amount, multiple beneficiaries."""

from app.domain.aml import (
    MULTIPLE_BENEFICIARIES,
    RAPID_MOVEMENT,
    STRUCTURING,
    UNUSUAL_AMOUNT,
    evaluate_aml,
)


def test_structuring_and_rapid_movement_fire_on_velocity():
    context = {"payments_last_hour": 3, "payments_last_10_min": 5, "amount_minor": 100}
    assert STRUCTURING in evaluate_aml(context)
    assert RAPID_MOVEMENT in evaluate_aml(context)


def test_unusual_amount_uses_the_configured_threshold():
    context = {"amount_minor": 4_000_000, "payments_last_hour": 0, "payments_last_10_min": 0}
    assert UNUSUAL_AMOUNT not in evaluate_aml(context)
    assert UNUSUAL_AMOUNT in evaluate_aml(context, unusual_amount_threshold=3_000_000)


def test_quiet_context_triggers_nothing():
    assert evaluate_aml({"amount_minor": 100}) == []


def test_multiple_beneficiaries_rule():
    # Four distinct recipients in 24h stays under the default threshold of five.
    quiet = {"amount_minor": 100, "distinct_beneficiaries_24h": 4}
    assert MULTIPLE_BENEFICIARIES not in evaluate_aml(quiet)
    # Five distinct recipients is a structuring pattern even with small amounts.
    loud = {"amount_minor": 100, "distinct_beneficiaries_24h": 5}
    assert MULTIPLE_BENEFICIARIES in evaluate_aml(loud)


def test_multiple_beneficiaries_threshold_is_configurable():
    context = {"amount_minor": 100, "distinct_beneficiaries_24h": 3}
    assert MULTIPLE_BENEFICIARIES in evaluate_aml(
        context, multiple_beneficiaries_threshold=3,
    )
