import json
import logging
from datetime import datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.models import Alert, Budget, CostEvent
from app.timeutils import utcnow

logger = logging.getLogger(__name__)


def _period_start(period: str) -> datetime:
    """Return the start-of-period datetime for the given budget period."""
    now = utcnow()
    if period == "hourly":
        return now.replace(minute=0, second=0, microsecond=0)
    if period == "daily":
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == "weekly":
        start = now - timedelta(days=now.weekday())
        return start.replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == "monthly":
        return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def _current_spend(budget: Budget, db: Session) -> float:
    """Calculate total spend for a budget in its current period."""
    start = _period_start(budget.period)

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


def load_enforcement_state(db: Session) -> list[dict]:
    """Prefetch active enforcement budgets with their current-period spend.

    Called once per ingestion request so we don't re-run a SUM(cost) query for
    every budget for every event in a batch.
    """
    budgets = (
        db.query(Budget)
        .filter(
            Budget.is_active.is_(True),
            Budget.control_action.in_(["stop", "rate_limit", "model_downgrade"]),
        )
        .all()
    )
    return [{"budget": b, "spend": _current_spend(b, db)} for b in budgets]


def event_rejection_reason(
    state: list[dict],
    agent_name: str,
    workflow: str | None,
    swarm: str | None,
    cost: float,
    provider: str | None = None,
    model: str | None = None,
) -> str | None:
    """Check one event against prefetched enforcement state.

    Returns a rejection reason if a matching budget is already at/over its limit,
    otherwise accumulates `cost` into matching budgets (so a single batch can't
    overshoot) and returns None.
    """
    matched = []
    for entry in state:
        budget = entry["budget"]
        if not _scope_matches(budget, agent_name, workflow, swarm, provider, model):
            continue
        if budget.limit_amount > 0 and entry["spend"] >= budget.limit_amount:
            return (
                f"Budget '{budget.name}' exceeded: ${entry['spend']:.2f} / "
                f"${budget.limit_amount:.2f} ({budget.period})"
            )
        matched.append(entry)

    for entry in matched:
        entry["spend"] += cost
    return None


def check_budgets(db: Session) -> list[Alert]:
    """Evaluate all active budgets and create alerts for threshold breaches."""
    budgets = db.query(Budget).filter(Budget.is_active.is_(True)).all()
    new_alerts: list[Alert] = []

    for budget in budgets:
        spend = _current_spend(budget, db)
        if budget.limit_amount <= 0:
            continue

        percentage = (spend / budget.limit_amount) * 100
        period_start = _period_start(budget.period)

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
