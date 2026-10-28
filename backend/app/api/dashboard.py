from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.database import engine
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
from app.services.budget_checker import _current_spend
from app.services.cost_engine import unpriced_clause
from app.timeutils import utcnow

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


def _format_timestamp(granularity: str):
    """Return a portable timestamp grouping expression for SQLite and PostgreSQL."""
    if granularity == "hour":
        if engine.dialect.name == "sqlite":
            return func.strftime("%Y-%m-%d %H:00", CostEvent.timestamp)
        else:
            return func.date_trunc("hour", CostEvent.timestamp)
    else:  # day
        if engine.dialect.name == "sqlite":
            return func.strftime("%Y-%m-%d", CostEvent.timestamp)
        else:
            return func.date_trunc("day", CostEvent.timestamp)


def _bucket_iso(value) -> str:
    """Bucket start as UTC ISO-8601 with "Z", identical on SQLite and Postgres
    (SQLite returns a strftime string, Postgres a naive datetime)."""
    if isinstance(value, str):
        value = datetime.fromisoformat(value if len(value) > 10 else value + " 00:00")
    return value.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")


def _period_bounds(period: str) -> tuple[datetime, datetime]:
    """Return (start, end) for a named period."""
    now = utcnow()
    if period == "today":
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == "week":
        start = (now - timedelta(days=now.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
    elif period == "month":
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    elif period == "quarter":
        quarter_month = ((now.month - 1) // 3) * 3 + 1
        start = now.replace(
            month=quarter_month, day=1, hour=0, minute=0, second=0, microsecond=0
        )
    else:
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return start, now


def _months_back(start: datetime, months: int) -> datetime:
    month_index = start.year * 12 + (start.month - 1) - months
    return start.replace(year=month_index // 12, month=month_index % 12 + 1)


def _prev_period_bounds(period: str) -> tuple[datetime, datetime]:
    """Like-for-like previous window for change %: the previous period's
    start plus the same elapsed time (clamped to that period's end).

    Comparing today-so-far against *all* of yesterday made every morning
    look like a large drop; 'quarter' used to compare against yesterday.
    """
    start, now = _period_bounds(period)
    if period == "week":
        prev_start = start - timedelta(weeks=1)
    elif period == "month":
        prev_start = _months_back(start, 1)
    elif period == "quarter":
        prev_start = _months_back(start, 3)
    else:  # today / default
        prev_start = start - timedelta(days=1)
    prev_end = min(prev_start + (now - start), start)
    return prev_start, prev_end


def _pct_change(current: float, previous: float) -> float:
    if previous == 0:
        return 100.0 if current > 0 else 0.0
    return round(((current - previous) / previous) * 100, 2)


@router.get("/overview", response_model=DashboardOverview)
def dashboard_overview(period: str = Query("today"), db: Session = Depends(get_db)):
    start, end = _period_bounds(period)
    prev_start, prev_end = _prev_period_bounds(period)

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
    start, end = _period_bounds(period)

    ts_expr = _format_timestamp(granularity).label("ts")

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
            timestamp=_bucket_iso(row.ts),
            cost=round(float(row.cost), 6),
            requests=int(row.requests),
        )
        for row in rows
    ]
    return TimeseriesResponse(data=data)


@router.get("/top-agents", response_model=TopAgentsResponse)
def dashboard_top_agents(
    period: str = Query("today"),
    limit: int = Query(10, ge=1, le=100),
    db: Session = Depends(get_db),
):
    start, end = _period_bounds(period)

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
    start, end = _period_bounds(period)

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
    for b in budgets:
        spent = _current_spend(b, db)
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
