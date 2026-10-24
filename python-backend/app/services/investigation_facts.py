"""Read-only facts about one fraud case, gathered for the investigator agent.

Every function is scoped to the case under investigation: none takes an identifier the model
could choose, so a manipulated model cannot reach another customer's data. Results are
aggregates (counts, typical amounts, ages) rather than raw rows, which keeps personal data and
token use low. Text a customer typed (payment references, beneficiary labels) is returned under
``customerSuppliedText`` so the agent's instructions can treat it as data, never as instructions.
"""

from datetime import UTC, datetime, timedelta
from statistics import median
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.models import (
    AmlAlert,
    AuditLog,
    Beneficiary,
    CustomerAccount,
    FraudCase,
    KycApplication,
    Payment,
)

MINOR_UNITS = 100


def _money(amount_minor: int | None, currency: str) -> str:
    if amount_minor is None:
        return "unknown"
    return f"{amount_minor / MINOR_UNITS:,.2f} {currency}"


def _age_days(moment: datetime | None, now: datetime) -> int | None:
    return None if moment is None else max(0, (now - moment).days)


class CaseFacts:
    """The facts available about one case. Built once per investigation."""

    def __init__(self, session: AsyncSession, case: FraudCase, now: datetime | None = None) -> None:
        self.session = session
        self.case = case
        self.now = now or datetime.now(UTC)
        self._payment: Payment | None = None
        self._loaded_payment = False

    async def payment(self) -> Payment | None:
        if not self._loaded_payment:
            self._payment = (await self.session.get(Payment, self.case.payment_id)
                             if self.case.payment_id else None)
            self._loaded_payment = True
        return self._payment

    async def overview(self) -> dict[str, Any]:
        """The flagged payment and why the fraud rules flagged it."""
        payment = await self.payment()
        facts: dict[str, Any] = {
            "case": {
                "score": self.case.score, "level": self.case.level, "status": self.case.status,
                "triggeredRules": list(self.case.reasons or []),
                "openedDaysAgo": _age_days(self.case.created_at, self.now),
            },
        }
        if payment is None:
            facts["payment"] = None
            return facts
        facts["payment"] = {
            "amount": _money(payment.amount_minor, payment.currency),
            "status": payment.status,
            "createdDaysAgo": _age_days(payment.created_at, self.now),
            "customerSuppliedText": {"reference": payment.reference},
        }
        beneficiary = await self.session.get(Beneficiary, payment.beneficiary_id)
        if beneficiary is not None:
            facts["beneficiary"] = {
                "status": beneficiary.status,
                "addedDaysAgo": _age_days(beneficiary.created_at, self.now),
                "verified": beneficiary.verified_at is not None,
                "customerSuppliedText": {"label": beneficiary.label},
            }
        return facts

    async def payment_history(self, days: int = 90) -> dict[str, Any]:
        """The customer's payments over the window: outcomes and what a typical amount is."""
        days = max(1, min(days, 365))
        since = self.now - timedelta(days=days)
        rows = list((await self.session.scalars(
            select(Payment).where(Payment.customer_id == self.case.customer_id,
                                  Payment.created_at >= since)
        )).all())
        flagged = await self.payment()
        others = [row for row in rows if flagged is None or row.id != flagged.id]
        by_status: dict[str, int] = {}
        for row in others:
            by_status[row.status] = by_status.get(row.status, 0) + 1
        currency = flagged.currency if flagged else (others[0].currency if others else "USD")
        same_currency = sorted(row.amount_minor for row in others if row.currency == currency)
        history: dict[str, Any] = {
            "windowDays": days,
            "otherPayments": len(others),
            "byStatus": by_status,
        }
        if same_currency:
            history["typicalAmount"] = _money(int(median(same_currency)), currency)
            history["largestAmount"] = _money(same_currency[-1], currency)
            if flagged is not None:
                larger = sum(1 for amount in same_currency if amount >= flagged.amount_minor)
                history["paymentsAtLeastAsLargeAsFlagged"] = larger
        return history

    async def beneficiary_profile(self) -> dict[str, Any]:
        """Prior payments to this beneficiary, and how many customers pay its account."""
        payment = await self.payment()
        if payment is None:
            return {"available": False}
        beneficiary = await self.session.get(Beneficiary, payment.beneficiary_id)
        if beneficiary is None:
            return {"available": False}
        prior = list((await self.session.scalars(
            select(Payment).where(Payment.customer_id == self.case.customer_id,
                                  Payment.beneficiary_id == beneficiary.id,
                                  Payment.id != payment.id)
        )).all())
        outcomes: dict[str, int] = {}
        for row in prior:
            outcomes[row.status] = outcomes.get(row.status, 0) + 1
        # Many unrelated customers paying one destination account is a money-mule signal.
        payers = int(await self.session.scalar(
            select(func.count(func.distinct(Beneficiary.customer_id))).where(
                Beneficiary.destination_account_id == beneficiary.destination_account_id)
        ) or 0)
        destination = await self.session.get(CustomerAccount, beneficiary.destination_account_id)
        return {
            "available": True,
            "priorPaymentsToBeneficiary": len(prior),
            "priorOutcomes": outcomes,
            "customersPayingThisDestinationAccount": payers,
            "destinationAccountAgeDays": _age_days(destination.created_at if destination else None,
                                                   self.now),
            "destinationAccountStatus": destination.status if destination else None,
        }

    async def compliance(self) -> dict[str, Any]:
        """KYC standing and AML alerts for the customer (no names, no documents)."""
        kyc = await self.session.scalar(
            select(KycApplication).where(KycApplication.customer_id == self.case.customer_id)
            .order_by(KycApplication.created_at.desc()).limit(1)
        )
        alerts = list((await self.session.scalars(
            select(AmlAlert).where(AmlAlert.customer_id == self.case.customer_id)
            .order_by(AmlAlert.created_at.desc()).limit(20)
        )).all())
        return {
            "kyc": None if kyc is None else {
                "status": kyc.status, "country": kyc.country,
                "decidedDaysAgo": _age_days(kyc.decided_at, self.now),
            },
            "amlAlerts": [{"rule": alert.rule_code, "status": alert.status,
                           "daysAgo": _age_days(alert.created_at, self.now)} for alert in alerts],
        }

    async def account_activity(self) -> dict[str, Any]:
        """Account ages, recent sign-in activity, and the customer's other fraud cases."""
        accounts = list((await self.session.scalars(
            select(CustomerAccount).where(CustomerAccount.customer_id == self.case.customer_id)
        )).all())
        week_ago = self.now - timedelta(days=7)
        logins = dict((await self.session.execute(
            select(AuditLog.action, func.count()).where(
                AuditLog.actor_id == self.case.customer_id,
                AuditLog.action.in_(["auth.login", "auth.login_failed"]),
                AuditLog.created_at >= week_ago,
            ).group_by(AuditLog.action)
        )).all())
        other_cases = list((await self.session.scalars(
            select(FraudCase).where(FraudCase.customer_id == self.case.customer_id,
                                    FraudCase.id != self.case.id)
        )).all())
        outcomes: dict[str, int] = {}
        for other in other_cases:
            outcomes[other.status] = outcomes.get(other.status, 0) + 1
        return {
            "accounts": [{"type": account.account_type, "status": account.status,
                          "ageDays": _age_days(account.created_at, self.now)}
                         for account in accounts],
            "signInsLast7Days": int(logins.get("auth.login", 0)),
            "failedSignInsLast7Days": int(logins.get("auth.login_failed", 0)),
            "otherFraudCases": outcomes,
        }
