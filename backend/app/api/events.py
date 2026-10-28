import json
import logging
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.config import settings
from app.metrics import (
    BUDGET_STATE_LOAD_SECONDS,
    EVENTS_INGESTED,
    EVENTS_OVER_BUDGET,
    INGEST_BATCH_SIZE,
)
from app.models.models import Agent, CostEvent
from app.schemas.schemas import (
    EventIn,
    EventOut,
    EventsEnvelope,
    EventsIngestionResponse,
    PaginatedEvents,
)
from app.services.budget_checker import (
    accumulate_event,
    check_budgets,
    event_budget_violations,
    load_enforcement_state,
)
from app.services.cost_engine import cost_for, resolve_pricing_match
from app.timeutils import to_naive_utc, utcnow

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/events", tags=["events"])


def _event_cost(event: EventIn, db: Session) -> tuple[float, str]:
    """Return (cost, cost_source) for an incoming event."""
    if event.cost is not None:
        return event.cost, "client"
    match = resolve_pricing_match(event.provider, event.model, db)
    if match is None:
        logger.warning(
            "No pricing for provider=%s model=%s — stored as unpriced",
            event.provider,
            event.model,
        )
        return 0.0, "unpriced"
    pricing, exact = match
    cost = cost_for(pricing, event.tokens_input, event.tokens_output)
    return cost, "pricing" if exact else "estimated"


def _process_event(
    event: EventIn, db: Session, cost: float, cost_source: str | None = None
) -> CostEvent:
    """Create a CostEvent from incoming data, auto-creating agent if needed."""
    # Auto-discover: create agent if it doesn't exist
    agent = db.query(Agent).filter(Agent.name == event.agent_name).first()
    if agent is None:
        # Concurrent requests can both see "no agent" and race the INSERT. The
        # loser must reuse the winner's row, not fail (and silently drop) the
        # event — so the INSERT gets its own savepoint and a re-read on conflict.
        try:
            with db.begin_nested():
                agent = Agent(
                    name=event.agent_name,
                    swarm=event.swarm,
                    workflow=event.workflow,
                )
                db.add(agent)
                db.flush()
        except IntegrityError:
            agent = db.query(Agent).filter(Agent.name == event.agent_name).one()

    metadata_str = None
    if event.metadata is not None:
        metadata_str = json.dumps(event.metadata)

    timestamp = event.timestamp if event.timestamp else utcnow()

    cost_event = CostEvent(
        trace_id=event.trace_id,
        agent_id=agent.id,
        agent_name=event.agent_name,
        model_name=event.model,
        provider_name=event.provider,
        tokens_input=event.tokens_input,
        tokens_output=event.tokens_output,
        cost=cost,
        cost_source=cost_source,
        duration_ms=event.duration_ms,
        status=event.status,
        workflow=event.workflow,
        swarm=event.swarm,
        metadata_json=metadata_str,
        timestamp=timestamp,
    )
    db.add(cost_event)
    return cost_event


def _begin_batch_transaction(db: Session) -> None:
    """Make the batch one transaction on SQLite too (OPS-07).

    pysqlite opens a transaction only at the first write statement, so the per-event
    SAVEPOINT below started a transaction of its own and its RELEASE
    committed it: a batch that failed half-way kept its first events, and the
    SDK (which retries 5xx) then sent the whole batch again. Opening the
    write transaction up front makes the savepoints nest inside it, as on
    Postgres. IMMEDIATE takes the write lock now (waiting up to the busy
    timeout) instead of failing on a read-to-write upgrade under concurrency.

    The lock is held for the whole batch: a concurrent writer waits up to the
    SQLite busy timeout (pysqlite default 5 s; `?timeout=N` on the URL) and
    then gets "database is locked" -> 500 with nothing stored, which the SDK
    retries. Keep that timeout below the client's read timeout, or a client
    may give up and resend a batch that later commits.
    """
    if db.get_bind().dialect.name != "sqlite":
        return
    # Already in a transaction (an earlier write on this session): the
    # savepoints nest inside it, and a second BEGIN would fail.
    if db.connection().connection.dbapi_connection.in_transaction:
        return
    db.execute(text("BEGIN IMMEDIATE"))


@router.post("", response_model=EventsIngestionResponse)
def ingest_events(
    body: EventsEnvelope | list[EventIn] | EventIn,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    if isinstance(body, EventsEnvelope):
        events = body.events
    elif isinstance(body, list):
        events = body
    else:
        events = [body]

    if len(events) > settings.MAX_EVENTS_PER_REQUEST:
        raise HTTPException(
            status_code=413,
            detail=f"Batch too large: {len(events)} events "
            f"(max {settings.MAX_EVENTS_PER_REQUEST})",
        )

    INGEST_BATCH_SIZE.observe(len(events))
    if events:
        _begin_batch_transaction(db)

    processed = 0
    errored = 0
    over_budget = 0
    budget_violations: list[str] = []
    created: list[CostEvent] = []

    # Prefetch enforcement budgets + baseline spend once for the whole batch.
    with BUDGET_STATE_LOAD_SECONDS.time():
        enforcement = load_enforcement_state(db)

    for event in events:
        cost, cost_source = _event_cost(event, db)

        # The call already happened: always record it. If a blocking budget
        # was already exhausted, flag the event (enforcement is pre-call).
        violations = event_budget_violations(
            enforcement,
            event.agent_name,
            event.workflow,
            event.swarm,
            event.timestamp,
            provider=event.provider,
            model=event.model,
        )

        # Isolate each event in a savepoint so one failure can't poison the batch.
        try:
            with db.begin_nested():
                cost_event = _process_event(event, db, cost, cost_source)
                cost_event.over_budget = bool(violations)
                db.flush()  # materialise the INSERT so cost_event.id is populated
            created.append(cost_event)
            processed += 1
            over_budget += bool(violations)
            for v in violations:
                if v not in budget_violations:
                    budget_violations.append(v)
            # Only stored spend counts toward later events in this batch.
            accumulate_event(
                enforcement,
                event.agent_name,
                event.workflow,
                event.swarm,
                event.timestamp,
                cost,
                provider=event.provider,
                model=event.model,
            )
        except Exception:
            errored += 1
            logger.exception("Failed to process event")

    # IDs are assigned by the savepoint flush above — collect them now (before
    # commit expires the instances) rather than racing a post-commit
    # "ORDER BY id DESC LIMIT n" query.
    event_ids = [e.id for e in created]
    db.commit()

    # Check budgets in background
    def _bg_check():
        from app.database import SessionLocal

        bg_db = SessionLocal()
        try:
            check_budgets(bg_db)
        except Exception:
            # Background tasks run in sequence and stop at the first error:
            # a failed check (e.g. a locked SQLite database) must not skip
            # the embedding task. Alerts are re-evaluated on the next batch.
            logger.warning("Background budget check failed", exc_info=True)
        finally:
            bg_db.close()

    background_tasks.add_task(_bg_check)

    # Embed new events in background
    if event_ids:

        def _bg_embed(ids: list[int]):
            from app.database import SessionLocal
            from app.services.vector_store import embed_and_upsert_events

            bg_db = SessionLocal()
            try:
                embed_and_upsert_events(bg_db, ids)
            except Exception:
                logger.warning("Background embedding failed", exc_info=True)
            finally:
                bg_db.close()

        background_tasks.add_task(_bg_embed, event_ids)

    EVENTS_INGESTED.labels(status="processed").inc(processed)
    # "rejected" is no longer produced (spend is always recorded); the series
    # stays at 0 for dashboard compatibility. Over-budget events are a SUBSET
    # of processed, so they get their own counter rather than an outcome label.
    EVENTS_OVER_BUDGET.inc(over_budget)
    # Savepoint failures are the outcome that signals the SERVICE is broken —
    # invisible in processed/rejected, so they get their own bounded label.
    EVENTS_INGESTED.labels(status="errored").inc(errored)

    return EventsIngestionResponse(
        received=len(events),
        processed=processed,
        rejected=0,
        errored=errored,
        over_budget=over_budget,
        budget_violations=budget_violations,
    )


@router.get("", response_model=PaginatedEvents)
def list_events(
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=500),
    agent_name: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    status: str | None = None,
    from_date: datetime | None = None,
    to_date: datetime | None = None,
    db: Session = Depends(get_db),
):
    query = db.query(CostEvent)
    from_date = to_naive_utc(from_date)
    to_date = to_naive_utc(to_date)

    if agent_name:
        query = query.filter(CostEvent.agent_name == agent_name)
    if provider:
        query = query.filter(CostEvent.provider_name == provider)
    if model:
        query = query.filter(CostEvent.model_name == model)
    if status:
        query = query.filter(CostEvent.status == status)
    if from_date:
        query = query.filter(CostEvent.timestamp >= from_date)
    if to_date:
        query = query.filter(CostEvent.timestamp <= to_date)

    total = query.count()
    items = (
        query.order_by(CostEvent.timestamp.desc())
        .offset((page - 1) * per_page)
        .limit(per_page)
        .all()
    )

    return PaginatedEvents(
        items=[EventOut.model_validate(e) for e in items],
        total=total,
        page=page,
        per_page=per_page,
    )
