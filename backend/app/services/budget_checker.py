import json
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.models import Alert, Budget, CostEvent
from app.timeutils import UTC_ZONE, deployment_tz, local_floor

logger = logging.getLogger(__name__)


_PERIOD_UNIT = {"hourly": "hour", "daily": "day", "weekly": "week", "monthly": "month"}


def _period_start(period: str, tz: ZoneInfo = UTC_ZONE) -> datetime:
    """Start (naive UTC) of the budget's current period. Periods follow the
    deployment time zone (D2): a daily budget resets at local midnight, a
    weekly one on local Monday, a monthly one on the local 1st."""
    return local_floor(_PERIOD_UNIT.get(period, "day"), tz)


def _current_spend(
    budget: Budget,
    db: Session,
    tz: ZoneInfo | None = None,
    start: datetime | None = None,
) -> float:
    """Calculate total spend for a budget in its current period (or since
    ``start`` when the caller already computed the period start)."""
    if start is None:
        start = _period_start(budget.period, tz or deployment_tz(db))

    query = db.query(func.coalesce(func.sum(CostEvent.cost), 0.0)).filter(
        CostEvent.timestamp >= start
    )

    if budget.scope == "agent" and budget.scope_ref:
        query = query.filter(CostEvent.agent_name == budget.scope_ref)
    elif budget.scope == "swarm" and budget.scope_ref:
        query = query.filter(CostEvent.swarm == budget.scope_ref)
    elif budget.scope == "workflow" and budget.scope_ref:
        query = query.filter(CostEvent.workflow == budget.scope_ref)
    elif budget.scope == "provider" and budget.scope_ref:
        # Provider spellings differ between SDK ("openai") and UI ("OpenAI").
        query = query.filter(
            func.lower(CostEvent.provider_name) == budget.scope_ref.lower()
        )
    elif budget.scope == "model" and budget.scope_ref:
        query = query.filter(
            func.lower(CostEvent.model_name) == budget.scope_ref.lower()
        )
    # scope == "global" -> no extra filter

    result = query.scalar()
    return float(result) if result else 0.0


def _alert_exists_for_period(
    budget_id: int, threshold: int, period_start: datetime, db: Session
) -> bool:
    """Check if an alert for this budget+threshold already exists in this period."""
    return (
        db.query(Alert)
        .filter(
            Alert.budget_id == budget_id,
            Alert.created_at >= period_start,
            # Match the unambiguous marker emitted in the message, not a bare
            # "{n}%" substring which collides across thresholds.
            Alert.message.contains(f"(threshold: {threshold}%)"),
        )
        .first()
        is not None
    )


def _scope_matches(
    budget: Budget,
    agent_name: str | None,
    workflow: str | None,
    swarm: str | None,
    provider: str | None = None,
    model: str | None = None,
) -> bool:
    """Same matching rules as _current_spend uses to sum the budget's spend."""
    ref = budget.scope_ref
    if not ref:
        return True  # global (or a legacy scoped row without a ref)
    if budget.scope == "agent":
        return ref == agent_name
    if budget.scope == "swarm":
        return ref == swarm
    if budget.scope == "workflow":
        return ref == workflow
    if budget.scope == "provider":
        return provider is not None and ref.lower() == provider.lower()
    if budget.scope == "model":
        return model is not None and ref.lower() == model.lower()
    return True  # scope == "global" matches everything


# Budget control actions (D1, docs/PLAN.md). Spend is ALWAYS recorded —
# rejecting an event at ingestion cannot undo a call that already happened,
# it only hides the cost. Enforcement is a pre-call check
# (GET /budgets/check, SDK ``enforce_budgets``): every action other than
# "alert" blocks further calls once the budget is exhausted. "throttle",
# "rate_limit" and "model_downgrade" have no throttling/downgrade behaviour
# and are treated as "block" (kept for compatibility).
ALERT_ONLY = "alert"


def is_blocking(budget: Budget) -> bool:
    return (budget.control_action or ALERT_ONLY) != ALERT_ONLY


def _violation(budget: Budget, spend: float) -> dict:
    return {
        "budget_id": budget.id,
        "name": budget.name,
        "scope": budget.scope,
        "scope_ref": budget.scope_ref,
        "period": budget.period,
        "limit_amount": budget.limit_amount,
        "spent": round(spend, 6),
        "message": (
            f"Budget '{budget.name}' exhausted: ${spend:.2f} / "
            f"${budget.limit_amount:.2f} ({budget.period})"
        ),
    }


def load_enforcement_state(db: Session) -> list[dict]:
    """Prefetch active blocking budgets with their current-period spend.

    Called once per ingestion request so we don't re-run a SUM(cost) query for
    every budget for every event in a batch.
    """
    budgets = db.query(Budget).filter(Budget.is_active.is_(True)).all()
    tz = deployment_tz(db)
    state = []
    for b in budgets:
        if not is_blocking(b):
            continue
        # One period start for both the spend sum and the in-period test, so
        # a batch at a period boundary cannot mix two periods.
        start = _period_start(b.period, tz)
        state.append(
            {"budget": b, "spend": _current_spend(b, db, start=start), "start": start}
        )
    return state


def _in_current_period(entry: dict, timestamp: datetime | None) -> bool:
    """Only spend timestamped inside the budget's current period counts toward
    it (a back-dated/replayed event belongs to an earlier period)."""
    return timestamp is None or timestamp >= entry["start"]


def event_budget_violations(
    state: list[dict],
    agent_name: str,
    workflow: str | None,
    swarm: str | None,
    timestamp: datetime | None = None,
    provider: str | None = None,
    model: str | None = None,
) -> list[str]:
    """Blocking budgets that were already exhausted when this (already
    incurred) event arrived — the call should have been blocked pre-call.

    Pure check: the caller adds the event's cost with
    :func:`accumulate_event` only once the event is actually stored.
    """
    violations = []
    for entry in state:
        budget = entry["budget"]
        if not _scope_matches(budget, agent_name, workflow, swarm, provider, model):
            continue
        if not _in_current_period(entry, timestamp):
            continue
        if budget.limit_amount > 0 and entry["spend"] >= budget.limit_amount:
            violations.append(_violation(budget, entry["spend"])["message"])
    return violations


def accumulate_event(
    state: list[dict],
    agent_name: str,
    workflow: str | None,
    swarm: str | None,
    timestamp: datetime | None,
    cost: float,
    provider: str | None = None,
    model: str | None = None,
) -> None:
    """Add a stored event's cost to the running spend of matching budgets, so
    later events in the same batch see it."""
    for entry in state:
        budget = entry["budget"]
        if _scope_matches(
            budget, agent_name, workflow, swarm, provider, model
        ) and _in_current_period(entry, timestamp):
            entry["spend"] += cost


def precall_check(
    db: Session,
    agent_name: str | None,
    workflow: str | None,
    swarm: str | None,
    provider: str | None = None,
    model: str | None = None,
) -> list[dict]:
    """Blocking budgets that would be violated by a new call with this
    attribution. Scope matching is the same function ingestion uses, so a
    budget flags ingested events exactly when it blocks calls: global always
    matches; agent/swarm/workflow/provider/model budgets match only when that
    attribute is supplied (a legacy scoped row with no scope_ref counts as
    global, as its spend does)."""
    violations = []
    tz = deployment_tz(db)
    for budget in db.query(Budget).filter(Budget.is_active.is_(True)).all():
        if not is_blocking(budget) or budget.limit_amount <= 0:
            continue
        if not _scope_matches(budget, agent_name, workflow, swarm, provider, model):
            continue
        spend = _current_spend(budget, db, tz)
        if spend >= budget.limit_amount:
            violations.append(_violation(budget, spend))
    return violations


def check_budgets(db: Session) -> list[Alert]:
    """Evaluate all active budgets and create alerts for threshold breaches."""
    budgets = db.query(Budget).filter(Budget.is_active.is_(True)).all()
    new_alerts: list[Alert] = []
    tz = deployment_tz(db)

    for budget in budgets:
        spend = _current_spend(budget, db, tz)
        if budget.limit_amount <= 0:
            continue

        percentage = (spend / budget.limit_amount) * 100
        period_start = _period_start(budget.period, tz)

        try:
            thresholds = json.loads(budget.alert_thresholds)
        except (json.JSONDecodeError, TypeError):
            thresholds = [50, 75, 90, 100]

        for threshold in sorted(thresholds):
            if percentage >= threshold:
                if _alert_exists_for_period(budget.id, threshold, period_start, db):
                    continue

                severity = "info"
                if threshold >= 100:
                    severity = "critical"
                    alert_type = "budget_exceeded"
                elif threshold >= 75:
                    severity = "warning"
                    alert_type = "budget_warning"
                else:
                    alert_type = "budget_warning"

                message = (
                    f"Budget '{budget.name}' ({budget.scope}) has reached "
                    f"{percentage:.1f}% of its ${budget.limit_amount:.2f} "
                    f"{budget.period} limit (threshold: {threshold}%). "
                    f"Current spend: ${spend:.2f}"
                )

                alert = Alert(
                    budget_id=budget.id,
                    alert_type=alert_type,
                    message=message,
                    severity=severity,
                )
                db.add(alert)
                new_alerts.append(alert)

    if new_alerts:
        db.commit()
        for alert in new_alerts:
            db.refresh(alert)
            try:
                from app.services.alert_service import dispatch_alert

                dispatch_alert(alert, db)
            except Exception:
                logger.exception("Failed to dispatch alert %s", alert.id)

    return new_alerts
