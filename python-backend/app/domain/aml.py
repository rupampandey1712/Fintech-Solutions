"""Anti-money-laundering heuristics: pure rule evaluation over a payment context."""

STRUCTURING = "structuring"
RAPID_MOVEMENT = "rapid_movement"
UNUSUAL_AMOUNT = "unusual_amount"
MULTIPLE_BENEFICIARIES = "multiple_beneficiaries"

DEFAULT_UNUSUAL_AMOUNT_THRESHOLD = 5_000_000
DEFAULT_MULTIPLE_BENEFICIARIES_THRESHOLD = 5


def evaluate_aml(
    context: dict,
    unusual_amount_threshold: int = DEFAULT_UNUSUAL_AMOUNT_THRESHOLD,
    multiple_beneficiaries_threshold: int = DEFAULT_MULTIPLE_BENEFICIARIES_THRESHOLD,
) -> list[str]:
    triggered: list[str] = []
    if context.get("payments_last_hour", 0) >= 3:
        triggered.append(STRUCTURING)
    if context.get("payments_last_10_min", 0) >= 5:
        triggered.append(RAPID_MOVEMENT)
    if context.get("amount_minor", 0) > unusual_amount_threshold:
        triggered.append(UNUSUAL_AMOUNT)
    if context.get("distinct_beneficiaries_24h", 0) >= multiple_beneficiaries_threshold:
        triggered.append(MULTIPLE_BENEFICIARIES)
    return triggered
