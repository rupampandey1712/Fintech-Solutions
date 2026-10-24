"""Reporting endpoints computed from queried rows in Python."""

import csv
import io
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import Customer, effective_roles, get_current_customer
from app.infrastructure.database import get_session
from app.infrastructure.models import (
    AmlAlert,
    FraudCase,
    JournalEntry,
    JournalLine,
    Payment,
    ReconciliationRun,
    SettlementBatch,
)
from app.services.report_storage import save_report

router = APIRouter(prefix="/api/v1/reports", tags=["reports"])


class PaymentTotals(BaseModel):
    payments: int
    completed: int
    failed: int
    blocked: int


class FraudTotals(BaseModel):
    open: int
    total: int


class AmlTotals(BaseModel):
    open: int
    total: int


class MonthlyVolumePoint(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    month: str
    currency: str
    count: int
    volume_minor: int


class DashboardResponse(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    totals: PaymentTotals
    success_rate: float | None
    fraud_cases: FraudTotals
    aml_alerts: AmlTotals
    monthly_volume: list[MonthlyVolumePoint]


class DailySeriesPoint(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    day: str
    currency: str
    count: int
    volume_minor: int


class TransactionsSeriesResponse(BaseModel):
    series: list[DailySeriesPoint]


def _bucket_by_currency(payments: list[Payment], keys: list[str],
                        key_of) -> list[tuple[str, str, int, int]]:
    """(key, currency, count, volume) for every key, once per currency seen within the keys.

    Amounts in different currencies are never added together. Each currency with activity in
    the window gets its own zero-filled series, so a chart can plot one currency at a time.
    """
    wanted = set(keys)
    totals: dict[tuple[str, str], list[int]] = {}
    for payment in payments:
        if payment.created_at is None:
            continue
        key = key_of(payment.created_at)
        if key not in wanted:
            continue
        bucket = totals.setdefault((key, payment.currency), [0, 0])
        bucket[0] += 1
        bucket[1] += payment.amount_minor
    currencies = sorted({currency for _, currency in totals})
    return [
        (key, currency, *totals.get((key, currency), (0, 0)))
        for key in keys for currency in currencies
    ]


def _is_admin(customer: Customer) -> bool:
    return "admin" in effective_roles(customer)


@router.get("/dashboard", response_model=DashboardResponse)
async def dashboard(
    customer: Customer = Depends(get_current_customer),
    session: AsyncSession = Depends(get_session),
) -> DashboardResponse:
    payment_query = select(Payment)
    if not _is_admin(customer):
        payment_query = payment_query.where(Payment.customer_id == customer.id)
    payments = list((await session.scalars(payment_query)).all())
    completed = sum(1 for payment in payments if payment.status == "completed")
    failed = sum(1 for payment in payments if payment.status == "failed")
    blocked = sum(1 for payment in payments if payment.risk_decision == "block")
    success_rate = round(completed * 100 / len(payments), 2) if payments else None

    fraud_query = select(FraudCase)
    aml_query = select(AmlAlert)
    if not _is_admin(customer):
        fraud_query = fraud_query.where(FraudCase.customer_id == customer.id)
        aml_query = aml_query.where(AmlAlert.customer_id == customer.id)
    fraud_cases = list((await session.scalars(fraud_query)).all())
    aml_alerts = list((await session.scalars(aml_query)).all())

    now = datetime.now(UTC)
    # Calendar arithmetic: stepping back 31-day blocks from the 1st skipped months (Oct 1 - 31
    # days is Aug 31), so the six buckets could miss the previous month entirely.
    month_keys = []
    for offset in range(5, -1, -1):
        year, month = divmod(now.year * 12 + now.month - 1 - offset, 12)
        month_keys.append(f"{year:04d}-{month + 1:02d}")
    months = _bucket_by_currency(payments, month_keys, lambda at: at.strftime("%Y-%m"))
    return DashboardResponse(
        totals=PaymentTotals(
            payments=len(payments), completed=completed, failed=failed, blocked=blocked,
        ),
        success_rate=success_rate,
        fraud_cases=FraudTotals(
            open=sum(1 for case in fraud_cases if case.status == "open"), total=len(fraud_cases),
        ),
        aml_alerts=AmlTotals(
            open=sum(1 for alert in aml_alerts if alert.status == "open"), total=len(aml_alerts),
        ),
        monthly_volume=[MonthlyVolumePoint(month=key, currency=currency, count=count,
                                           volume_minor=volume)
                        for key, currency, count, volume in months],
    )


@router.get("/transactions", response_model=TransactionsSeriesResponse)
async def transaction_series(
    customer: Customer = Depends(get_current_customer),
    session: AsyncSession = Depends(get_session),
    days: int = Query(default=30, ge=1, le=90),
) -> TransactionsSeriesResponse:
    query = select(Payment).where(
        Payment.created_at >= datetime.now(UTC) - timedelta(days=days)
    )
    if not _is_admin(customer):
        query = query.where(Payment.customer_id == customer.id)
    payments = list((await session.scalars(query)).all())
    now = datetime.now(UTC)
    day_keys = [
        (now - timedelta(days=offset)).strftime("%Y-%m-%d") for offset in range(days - 1, -1, -1)
    ]
    series = _bucket_by_currency(payments, day_keys, lambda at: at.strftime("%Y-%m-%d"))
    return TransactionsSeriesResponse(series=[
        DailySeriesPoint(day=key, currency=currency, count=count, volume_minor=volume)
        for key, currency, count, volume in series
    ])


# --------------------------------------------------------------------------------------------
# Fraud and finance dashboards (spec section 42)

class FraudDashboardResponse(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    cases_by_status: dict[str, int]
    risk_distribution: dict[str, int]
    blocked_payments: int
    top_rules: list[dict[str, Any]]


class FinanceDashboardResponse(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    settlement: dict[str, int]
    reconciliation: dict[str, int]
    transaction_volume_minor: int
    payment_count: int


DASHBOARD_ROLES = ("admin", "fraud_analyst", "finance_user", "operations_user",
                   "compliance_officer")


@router.get("/fraud", response_model=FraudDashboardResponse)
async def fraud_dashboard(
    customer: Customer = Depends(get_current_customer),
    session: AsyncSession = Depends(get_session),
) -> FraudDashboardResponse:
    """Fraud cases by status, risk-score distribution, blocks, and the most-triggered rules."""
    if not (set(DASHBOARD_ROLES) & set(effective_roles(customer))):
        raise HTTPException(status_code=403, detail="forbidden")
    cases = list((await session.scalars(select(FraudCase))).all())
    payments = list((await session.scalars(select(Payment))).all())
    cases_by_status: dict[str, int] = {}
    for case in cases:
        cases_by_status[case.status] = cases_by_status.get(case.status, 0) + 1
    risk_distribution: dict[str, int] = {}
    for payment in payments:
        level = payment.risk_level or "low"
        risk_distribution[level] = risk_distribution.get(level, 0) + 1
    rule_counts: dict[str, int] = {}
    for case in cases:
        for reason in case.reasons or []:
            rule_counts[reason] = rule_counts.get(reason, 0) + 1
    top_rules = [
        {"rule": rule, "triggered": count}
        for rule, count in sorted(rule_counts.items(), key=lambda item: -item[1])[:5]
    ]
    return FraudDashboardResponse(
        cases_by_status=cases_by_status,
        risk_distribution=risk_distribution,
        blocked_payments=sum(1 for payment in payments if payment.risk_decision == "block"),
        top_rules=top_rules,
    )


@router.get("/finance", response_model=FinanceDashboardResponse)
async def finance_dashboard(
    customer: Customer = Depends(get_current_customer),
    session: AsyncSession = Depends(get_session),
) -> FinanceDashboardResponse:
    """Settlement, reconciliation and volume totals for the finance dashboard."""
    if not ({"admin", "finance_user", "operations_user"} & set(effective_roles(customer))):
        raise HTTPException(status_code=403, detail="forbidden")
    batches = list((await session.scalars(select(SettlementBatch))).all())
    runs = list((await session.scalars(select(ReconciliationRun))).all())
    payments = list((await session.scalars(select(Payment))).all())
    settlement = {
        "batches": len(batches),
        "completed": sum(1 for batch in batches if batch.status == "completed"),
        "failed": sum(1 for batch in batches if batch.status == "failed"),
        "settledVolumeMinor": sum(batch.total_minor for batch in batches),
    }
    latest_run = runs[0] if runs else None
    reconciliation = {
        "runs": len(runs),
        "matched": latest_run.matched if latest_run else 0,
        "amountMismatch": latest_run.amount_mismatch if latest_run else 0,
        "statusMismatch": latest_run.status_mismatch if latest_run else 0,
        "missingExternal": latest_run.missing_external if latest_run else 0,
        "missingInternal": latest_run.missing_internal if latest_run else 0,
    }
    return FinanceDashboardResponse(
        settlement=settlement,
        reconciliation=reconciliation,
        transaction_volume_minor=sum(payment.amount_minor for payment in payments),
        payment_count=len(payments),
    )


# --------------------------------------------------------------------------------------------
# CSV export (spec section 62)

EXPORT_ROLES = ("admin", "finance_user", "operations_user", "fraud_analyst",
                "compliance_officer")


def _csv_response(rows: list[list[Any]], filename: str, stored_path: str | None) -> Response:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    for row in rows:
        writer.writerow([str(value) for value in row])
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    if stored_path:
        headers["X-Report-Path"] = stored_path
    return Response(content=buffer.getvalue(), media_type="text/csv", headers=headers)


async def _export_rows(session: AsyncSession, report_type: str) -> list[list[Any]]:
    """Build the rows of a named report; the first row is always the CSV header."""
    if report_type == "daily-transactions":
        payments = list((await session.scalars(select(Payment))).all())
        by_day: dict[str, list[int]] = {}
        for payment in payments:
            key = (payment.created_at or datetime.now(UTC)).strftime("%Y-%m-%d")
            bucket = by_day.setdefault(key, [0, 0, 0, 0])
            bucket[0] += 1
            bucket[1] += payment.amount_minor
            if payment.status == "completed":
                bucket[2] += 1
            elif payment.status == "failed":
                bucket[3] += 1
        return [
            ["day", "payments", "volume_minor", "completed", "failed"],
            *[[day, *values] for day, values in sorted(by_day.items())],
        ]
    if report_type == "payment-success":
        payments = list((await session.scalars(select(Payment))).all())
        counts: dict[str, int] = {}
        for payment in payments:
            counts[payment.status] = counts.get(payment.status, 0) + 1
        total = sum(counts.values()) or 1
        return [
            ["status", "count", "share_percent"],
            *[[status, count, round(count * 100 / total, 2)]
              for status, count in sorted(counts.items())],
        ]
    if report_type == "fraud":
        cases = list((await session.scalars(select(FraudCase))).all())
        return [
            ["case_id", "payment_id", "score", "level", "status", "reasons", "created_at"],
            *[[case.id, case.payment_id, case.score, case.level, case.status,
               "|".join(case.reasons or []), case.created_at] for case in cases],
        ]
    if report_type == "aml":
        alerts = list((await session.scalars(select(AmlAlert))).all())
        return [
            ["alert_id", "customer_id", "payment_id", "rule_code", "status", "created_at"],
            *[[alert.id, alert.customer_id, alert.payment_id, alert.rule_code, alert.status,
               alert.created_at] for alert in alerts],
        ]
    if report_type == "reconciliation":
        runs = list((await session.scalars(
            select(ReconciliationRun).order_by(ReconciliationRun.started_at.desc())
        )).all())
        return [
            ["run_id", "started_at", "finished_at", "matched", "amount_mismatch",
             "status_mismatch", "missing_external", "missing_internal"],
            *[[run.id, run.started_at, run.finished_at, run.matched, run.amount_mismatch,
               run.status_mismatch, run.missing_external, run.missing_internal]
              for run in runs],
        ]
    if report_type == "ledger":
        rows = (await session.execute(
            select(JournalEntry.business_reference, JournalEntry.currency,
                   JournalEntry.posted_at, JournalLine.side, JournalLine.amount_minor)
            .join(JournalLine, JournalLine.journal_id == JournalEntry.id)
            .order_by(JournalEntry.posted_at)
        )).all()
        return [
            ["business_reference", "currency", "posted_at", "side", "amount_minor"],
            *[[reference, currency, posted_at, side, amount]
              for reference, currency, posted_at, side, amount in rows],
        ]
    if report_type == "settlement":
        batches = list((await session.scalars(
            select(SettlementBatch).order_by(SettlementBatch.created_at.desc())
        )).all())
        return [
            ["batch_id", "business_date", "currency", "status", "payment_count",
             "total_minor", "fee_minor", "net_minor", "processed_at"],
            *[[batch.id, batch.business_date.strftime("%Y-%m-%d"), batch.currency, batch.status,
               batch.payment_count, batch.total_minor, batch.fee_minor,
               batch.total_minor - batch.fee_minor, batch.processed_at]
              for batch in batches],
        ]
    raise HTTPException(
        status_code=404,
        detail="Unknown report type; use daily-transactions, payment-success, fraud, aml, "
               "reconciliation, ledger or settlement",
    )


@router.get("/{report_type}/export")
async def export_report(
    report_type: str,
    customer: Customer = Depends(get_current_customer),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Download a named report as CSV; the generated file is also stored via blob/filesystem."""
    staff_only = report_type in {"fraud", "aml", "reconciliation", "settlement"}
    if staff_only and not (set(EXPORT_ROLES) & set(effective_roles(customer))):
        raise HTTPException(status_code=403, detail="forbidden")
    rows = await _export_rows(session, report_type)
    filename = f"{report_type}-{datetime.now(UTC).strftime('%Y%m%d')}.csv"
    content = "\n".join(
        ",".join(str(value) for value in row) for row in rows
    ).encode("utf-8")
    stored_path = await save_report(filename, content)
    return _csv_response(rows, filename, stored_path)
