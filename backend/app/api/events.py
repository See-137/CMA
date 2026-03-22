import json
import logging
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.models.models import Agent, CostEvent
from app.schemas.schemas import (
    EventIn,
    EventOut,
    EventsIngestionResponse,
    PaginatedEvents,
)
from app.services.budget_checker import check_budget_enforcement, check_budgets
from app.services.cost_engine import calculate_cost

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/events", tags=["events"])


def _process_event(event: EventIn, db: Session) -> CostEvent:
    """Create a CostEvent from incoming data, auto-creating agent if needed."""
    # Auto-discover: create agent if it doesn't exist
    agent = db.query(Agent).filter(Agent.name == event.agent_name).first()
    if agent is None:
        agent = Agent(
            name=event.agent_name,
            swarm=event.swarm,
            workflow=event.workflow,
        )
        db.add(agent)
        db.flush()

    # Calculate cost if not provided
    cost = event.cost
    if cost is None:
        cost = calculate_cost(
            event.provider, event.model, event.tokens_input, event.tokens_output, db
        )

    metadata_str = None
    if event.metadata is not None:
        metadata_str = json.dumps(event.metadata)

    timestamp = event.timestamp if event.timestamp else datetime.utcnow()

    cost_event = CostEvent(
        trace_id=event.trace_id,
        agent_id=agent.id,
        agent_name=event.agent_name,
        model_name=event.model,
        provider_name=event.provider,
        tokens_input=event.tokens_input,
        tokens_output=event.tokens_output,
        cost=cost,
        duration_ms=event.duration_ms,
        status=event.status,
        workflow=event.workflow,
        swarm=event.swarm,
        metadata_json=metadata_str,
        timestamp=timestamp,
    )
    db.add(cost_event)
    return cost_event


@router.post("", response_model=EventsIngestionResponse)
def ingest_events(
    body: EventIn | list[EventIn],
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    events = body if isinstance(body, list) else [body]
    processed = 0
    rejected = 0
    rejection_reasons: list[str] = []

    for event in events:
        # Check budget enforcement BEFORE processing
        accepted, reason = check_budget_enforcement(
            agent_name=event.agent_name,
            workflow=event.workflow,
            swarm=event.swarm,
            db=db,
        )
        if not accepted:
            rejected += 1
            if reason and reason not in rejection_reasons:
                rejection_reasons.append(reason)
            continue

        try:
            _process_event(event, db)
            processed += 1
        except Exception:
            logger.exception("Failed to process event")

    db.commit()

    # Check budgets in background
    def _bg_check():
        from app.database import SessionLocal

        bg_db = SessionLocal()
        try:
            check_budgets(bg_db)
        finally:
            bg_db.close()

    background_tasks.add_task(_bg_check)

    return EventsIngestionResponse(
        received=len(events),
        processed=processed,
        rejected=rejected,
        rejection_reasons=rejection_reasons,
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
