"""Configurable fraud rules engine: pure functions over rule configs and payment context."""

from dataclasses import dataclass, field

LARGE_AMOUNT = "large_amount"
VELOCITY_BURST = "velocity_burst"
NEW_BENEFICIARY_HIGH_AMOUNT = "new_beneficiary_high_amount"
YOUNG_ACCOUNT_LARGE_TRANSFER = "young_account_large_transfer"
FAILED_ATTEMPTS = "failed_attempts"

LEVEL_LOW = "low"
LEVEL_MEDIUM = "medium"
LEVEL_HIGH = "high"
LEVEL_CRITICAL = "critical"
DECISION_ALLOW = "allow"
DECISION_REVIEW = "review"
DECISION_BLOCK = "block"

# Risk score is capped so pathological stacking of rules stays inside the level bands.
MAX_SCORE = 100


@dataclass(frozen=True, slots=True)
class FraudRuleConfig:
    code: str
    weight: int
    threshold_minor: int | None = None


@dataclass(frozen=True, slots=True)
class FraudEvaluation:
    score: int
    level: str
    decision: str
    reasons: list[str] = field(default_factory=list)


def level_for_score(score: int) -> str:
    if score <= 30:
        return LEVEL_LOW
    if score <= 60:
        return LEVEL_MEDIUM
    if score <= 80:
        return LEVEL_HIGH
    return LEVEL_CRITICAL


def decision_for_score(score: int) -> str:
    if score > 70:
        return DECISION_BLOCK
    if score >= 40:
        return DECISION_REVIEW
    return DECISION_ALLOW


def evaluate(rules: list[FraudRuleConfig], context: dict) -> FraudEvaluation:
    reasons: list[str] = []
    score = 0
    amount_minor = context.get("amount_minor", 0)
    for rule in rules:
        threshold = rule.threshold_minor
        triggered = False
        if rule.code == LARGE_AMOUNT:
            triggered = threshold is not None and amount_minor > threshold
        elif rule.code == VELOCITY_BURST:
            triggered = threshold is not None and context.get("payments_last_hour", 0) > threshold
        elif rule.code == NEW_BENEFICIARY_HIGH_AMOUNT:
            beneficiary_age = context.get("beneficiary_age_seconds")
            triggered = (
                threshold is not None
                and beneficiary_age is not None
                and beneficiary_age < 86_400
                and amount_minor > threshold
            )
        elif rule.code == YOUNG_ACCOUNT_LARGE_TRANSFER:
            account_age = context.get("account_age_seconds")
            # The threshold is the rule's configured value (admin-editable), with the historic
            # default as the fallback when a deployment leaves it unset.
            configured = threshold if threshold is not None else 1_000_000
            triggered = (
                account_age is not None and account_age < 86_400 and amount_minor > configured
            )
        elif rule.code == FAILED_ATTEMPTS:
            triggered = threshold is not None and context.get("failed_last_hour", 0) > threshold
        if triggered:
            score += rule.weight
            reasons.append(rule.code)
    score = min(score, MAX_SCORE)
    return FraudEvaluation(
        score=score, level=level_for_score(score), decision=decision_for_score(score),
        reasons=reasons,
    )
