from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.models.models import Budget, CostEvent
from app.schemas.schemas import (
    BudgetStatusItem,
    BudgetStatusResponse,
    DashboardOverview,
    ModelUsageItem,
    ModelUsageResponse,
    TimeseriesPoint,
    TimeseriesResponse,
    TopAgentItem,
    TopAgentsResponse,
)
from app.services.buckets import bucket_start_utc, local_bucket_expr
from app.services.budget_checker import _current_spend
from app.services.cost_engine import unpriced_clause
from app.timeutils import deployment_tz, local_floor, shift_local, utc_iso, utcnow

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


_PERIOD_UNIT = {"today": "day", "week": "week", "month": "month", "quarter": "quarter"}
# Previous like-for-like period, as a local wall-clock shift (D2).
_PREV_SHIFT = {
    "day": {"days": -1},
    "week": {"days": -7},
    "month": {"months": -1},
    "quarter": {"months": -3},
}


def _period_bounds(period: str, tz: ZoneInfo) -> tuple[datetime, datetime]:
    """(start, now) for a named period; the start is local midnight / local
    Monday / the local 1st in the deployment time zone (D2)."""
    now = utcnow()
    return local_floor(_PERIOD_UNIT.get(period, "day"), tz, now), now


def _prev_period_bounds(period: str, tz: ZoneInfo) -> tuple[datetime, datetime]:
    """Like-for-like previous window for change %: the previous period's
    start plus the same elapsed time (clamped to that period's end).

    Comparing today-so-far against *all* of yesterday made every morning
    look like a large drop; 'quarter' used to compare against yesterday.
    """
    start, now = _period_bounds(period, tz)
    prev_start = shift_local(start, tz, **_PREV_SHIFT[_PERIOD_UNIT.get(period, "day")])
    prev_end = min(prev_start + (now - start), start)
    return prev_start, prev_end


def _pct_change(current: float, previous: float) -> float:
    if previous == 0:
        return 100.0 if current > 0 else 0.0
    return round(((current - previous) / previous) * 100, 2)


@router.get("/overview", response_model=DashboardOverview)
def dashboard_overview(period: str = Query("today"), db: Session = Depends(get_db)):
    tz = deployment_tz(db)
    start, end = _period_bounds(period, tz)
    prev_start, prev_end = _prev_period_bounds(period, tz)

    # Current period
    current = (
        db.query(
            func.coalesce(func.sum(CostEvent.cost), 0.0).label("total_cost"),
            func.count(CostEvent.id).label("total_requests"),
            func.coalesce(
                func.sum(CostEvent.tokens_input + CostEvent.tokens_output), 0
            ).label("total_tokens"),
        )
        .filter(CostEvent.timestamp >= start, CostEvent.timestamp <= end)
        .first()
    )

    # Previous period
    prev = (
        db.query(
            func.coalesce(func.sum(CostEvent.cost), 0.0).label("total_cost"),
            func.count(CostEvent.id).label("total_requests"),
        )
        .filter(CostEvent.timestamp >= prev_start, CostEvent.timestamp < prev_end)
        .first()
    )

    active_agents = (
        db.query(func.count(func.distinct(CostEvent.agent_name)))
        .filter(CostEvent.timestamp >= start, CostEvent.timestamp <= end)
        .scalar()
    ) or 0

    unpriced_events = (
        db.query(func.count(CostEvent.id))
        .filter(
            CostEvent.timestamp >= start,
            CostEvent.timestamp <= end,
            unpriced_clause(),
        )
        .scalar()
    ) or 0

    return DashboardOverview(
        total_cost=round(float(current.total_cost), 6),
        unpriced_events=int(unpriced_events),
        total_requests=int(current.total_requests),
        active_agents=int(active_agents),
        total_tokens=int(current.total_tokens),
        cost_change_pct=_pct_change(float(current.total_cost), float(prev.total_cost)),
        request_change_pct=_pct_change(
            float(current.total_requests), float(prev.total_requests)
        ),
    )


@router.get("/timeseries", response_model=TimeseriesResponse)
def dashboard_timeseries(
    period: str = Query("week"),
    granularity: str = Query("day"),
    db: Session = Depends(get_db),
):
    tz = deployment_tz(db)
    start, end = _period_bounds(period, tz)
    unit = "hour" if granularity == "hour" else "day"
    ts_expr = local_bucket_expr(CostEvent.timestamp, unit, tz, db).label("ts")

    rows = (
        db.query(
            ts_expr,
            func.sum(CostEvent.cost).label("cost"),
            func.count(CostEvent.id).label("requests"),
        )
        .filter(CostEvent.timestamp >= start, CostEvent.timestamp <= end)
        .group_by(ts_expr)
        .order_by(ts_expr)
        .all()
    )

    data = [
        TimeseriesPoint(
            timestamp=utc_iso(bucket_start_utc(row.ts, unit, tz)),
            cost=round(float(row.cost), 6),
            requests=int(row.requests),
        )
        for row in rows
    ]
    return TimeseriesResponse(data=data, timezone=tz.key)


@router.get("/top-agents", response_model=TopAgentsResponse)
def dashboard_top_agents(
    period: str = Query("today"),
    limit: int = Query(10, ge=1, le=100),
    db: Session = Depends(get_db),
):
    start, end = _period_bounds(period, deployment_tz(db))

    rows = (
        db.query(
            CostEvent.agent_name,
            func.sum(CostEvent.cost).label("total_cost"),
            func.count(CostEvent.id).label("request_count"),
            func.avg(CostEvent.cost).label("avg_cost"),
        )
        .filter(CostEvent.timestamp >= start, CostEvent.timestamp <= end)
        .group_by(CostEvent.agent_name)
        .order_by(func.sum(CostEvent.cost).desc())
        .limit(limit)
        .all()
    )

    data = [
        TopAgentItem(
            agent_name=row.agent_name,
            total_cost=round(float(row.total_cost), 6),
            request_count=int(row.request_count),
            avg_cost=round(float(row.avg_cost), 6),
        )
        for row in rows
    ]
    return TopAgentsResponse(data=data)


@router.get("/model-usage", response_model=ModelUsageResponse)
def dashboard_model_usage(period: str = Query("today"), db: Session = Depends(get_db)):
    start, end = _period_bounds(period, deployment_tz(db))

    rows = (
        db.query(
            CostEvent.model_name,
            CostEvent.provider_name,
            func.sum(CostEvent.cost).label("total_cost"),
            func.count(CostEvent.id).label("request_count"),
            func.sum(CostEvent.tokens_input + CostEvent.tokens_output).label(
                "token_count"
            ),
        )
        .filter(CostEvent.timestamp >= start, CostEvent.timestamp <= end)
        .group_by(CostEvent.model_name, CostEvent.provider_name)
        .order_by(func.sum(CostEvent.cost).desc())
        .all()
    )

    data = [
        ModelUsageItem(
            model_name=row.model_name,
            provider=row.provider_name,
            total_cost=round(float(row.total_cost), 6),
            request_count=int(row.request_count),
            token_count=int(row.token_count),
        )
        for row in rows
    ]
    return ModelUsageResponse(data=data)


@router.get("/budget-status", response_model=BudgetStatusResponse)
def dashboard_budget_status(db: Session = Depends(get_db)):
    budgets = db.query(Budget).filter(Budget.is_active.is_(True)).all()

    data = []
    tz = deployment_tz(db)
    for b in budgets:
        spent = _current_spend(b, db, tz)
        pct = (spent / b.limit_amount * 100) if b.limit_amount > 0 else 0.0

        if pct >= 100:
            status = "exceeded"
        elif pct >= 75:
            status = "warning"
        else:
            status = "ok"

        data.append(
            BudgetStatusItem(
                budget_id=b.id,
                name=b.name,
                scope=b.scope,
                limit_amount=b.limit_amount,
                spent=round(spent, 6),
                percentage=round(pct, 2),
                status=status,
            )
        )

    return BudgetStatusResponse(data=data)
