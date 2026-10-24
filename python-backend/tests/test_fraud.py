from app.domain.fraud import (
    DECISION_ALLOW,
    DECISION_BLOCK,
    DECISION_REVIEW,
    FraudRuleConfig,
    evaluate,
    level_for_score,
)


def rules(**overrides):
    defaults = {
        "large_amount": FraudRuleConfig("large_amount", 30, 5_000_000),
        "velocity_burst": FraudRuleConfig("velocity_burst", 25, 3),
        "new_beneficiary_high_amount": FraudRuleConfig(
            "new_beneficiary_high_amount", 20, 2_000_000,
        ),
        "young_account_large_transfer": FraudRuleConfig(
            "young_account_large_transfer", 25, 1_000_000,
        ),
        "failed_attempts": FraudRuleConfig("failed_attempts", 20, 3),
    }
    defaults.update(overrides)
    return list(defaults.values())


def context(**overrides):
    defaults = {
        "amount_minor": 1_000,
        "beneficiary_age_seconds": 10_000_000,
        "account_age_seconds": 10_000_000,
        "payments_last_10_min": 0,
        "payments_last_hour": 0,
        "failed_last_hour": 0,
    }
    defaults.update(overrides)
    return defaults


def test_no_rules_triggered():
    result = evaluate(rules(), context())
    assert result.score == 0
    assert result.level == "low"
    assert result.decision == DECISION_ALLOW
    assert result.reasons == []


def test_large_amount_triggers():
    result = evaluate(rules(), context(amount_minor=5_000_001))
    assert result.reasons == ["large_amount"]
    assert result.score == 30


def test_large_amount_at_threshold_does_not_trigger():
    result = evaluate(rules(), context(amount_minor=5_000_000))
    assert "large_amount" not in result.reasons


def test_velocity_burst():
    assert "velocity_burst" in evaluate(rules(), context(payments_last_hour=4)).reasons
    assert "velocity_burst" not in evaluate(rules(), context(payments_last_hour=3)).reasons


def test_new_beneficiary_high_amount():
    ctx = context(amount_minor=2_000_001, beneficiary_age_seconds=3600)
    assert "new_beneficiary_high_amount" in evaluate(rules(), ctx).reasons
    assert "new_beneficiary_high_amount" not in evaluate(
        rules(), context(amount_minor=2_000_001, beneficiary_age_seconds=90_000)
    ).reasons


def test_young_account_large_transfer():
    ctx = context(amount_minor=1_100_000, account_age_seconds=100)
    assert "young_account_large_transfer" in evaluate(rules(), ctx).reasons
    assert "young_account_large_transfer" not in evaluate(
        rules(), context(amount_minor=1_100_000, account_age_seconds=90_000)
    ).reasons


def test_failed_attempts():
    assert "failed_attempts" in evaluate(rules(), context(failed_last_hour=4)).reasons
    assert "failed_attempts" not in evaluate(rules(), context(failed_last_hour=3)).reasons


def test_level_boundaries():
    assert level_for_score(30) == "low"
    assert level_for_score(31) == "medium"
    assert level_for_score(60) == "medium"
    assert level_for_score(61) == "high"
    assert level_for_score(80) == "high"
    assert level_for_score(81) == "critical"


def test_decision_boundaries():
    result = evaluate(rules(), context(amount_minor=5_000_001))
    assert result.score == 30 and result.decision == DECISION_ALLOW
    medium = evaluate(rules(), context(
        amount_minor=5_000_001, payments_last_hour=4,
    ))
    assert medium.score == 55 and medium.decision == DECISION_REVIEW
    high = evaluate(rules(), context(
        amount_minor=5_000_001, payments_last_hour=4, failed_last_hour=4,
    ))
    assert high.score == 75 and high.decision == DECISION_BLOCK
    critical = evaluate(rules(), context(
        amount_minor=5_000_001, payments_last_hour=4, failed_last_hour=4,
        account_age_seconds=100, beneficiary_age_seconds=100,
    ))
    assert critical.score == 100
    assert critical.level == "critical"
    assert critical.decision == DECISION_BLOCK


def test_score_capped_at_100():
    heavy = rules(
        large_amount=FraudRuleConfig("large_amount", 70, 1),
        velocity_burst=FraudRuleConfig("velocity_burst", 70, 0),
        failed_attempts=FraudRuleConfig("failed_attempts", 70, 0),
    )
    result = evaluate(heavy, context(amount_minor=10, payments_last_hour=1, failed_last_hour=1))
    assert result.score == 100
