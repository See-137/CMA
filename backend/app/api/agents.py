from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.models.models import Agent, CostEvent
from app.schemas.schemas import (
    AgentCreate,
    AgentDetail,
    AgentOut,
    AgentUpdate,
    EventOut,
    ModelUsedItem,
)

router = APIRouter(prefix="/agents", tags=["agents"])


@router.get("", response_model=list[AgentOut])
def list_agents(db: Session = Depends(get_db)):
    agents = db.query(Agent).filter(Agent.is_active.is_(True)).all()
    result = []
    for a in agents:
        stats = (
            db.query(
                func.coalesce(func.sum(CostEvent.cost), 0.0),
                func.count(CostEvent.id),
            )
            .filter(CostEvent.agent_id == a.id)
            .first()
        )
        item = AgentOut.model_validate(a)
        item.total_cost = float(stats[0])
        item.request_count = int(stats[1])
        result.append(item)
    return result


@router.post("", response_model=AgentOut, status_code=201)
def create_agent(body: AgentCreate, db: Session = Depends(get_db)):
    existing = db.query(Agent).filter(Agent.name == body.name).first()
    if existing:
        raise HTTPException(
            status_code=409, detail="Agent with this name already exists"
        )

    agent = Agent(
        name=body.name,
        description=body.description,
        environment=body.environment,
        swarm=body.swarm,
        workflow=body.workflow,
    )
    db.add(agent)
    db.commit()
    db.refresh(agent)
    return AgentOut.model_validate(agent)


@router.get("/{agent_id}", response_model=AgentDetail)
def get_agent(agent_id: int, db: Session = Depends(get_db)):
    agent = db.query(Agent).filter(Agent.id == agent_id).first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    # Aggregate stats
    stats = (
        db.query(
            func.coalesce(func.sum(CostEvent.cost), 0.0).label("total_cost"),
            func.count(CostEvent.id).label("total_requests"),
            func.coalesce(
                func.sum(CostEvent.tokens_input + CostEvent.tokens_output), 0
            ).label("total_tokens"),
        )
        .filter(CostEvent.agent_id == agent.id)
        .first()
    )

    # Models used breakdown
    models_rows = (
        db.query(
            CostEvent.model_name,
            CostEvent.provider_name,
            func.sum(CostEvent.cost).label("total_cost"),
            func.count(CostEvent.id).label("request_count"),
        )
        .filter(CostEvent.agent_id == agent.id)
        .group_by(CostEvent.model_name, CostEvent.provider_name)
        .all()
    )
    models_used = [
        ModelUsedItem(
            model_name=r.model_name,
            provider_name=r.provider_name,
            total_cost=float(r.total_cost),
            request_count=int(r.request_count),
        )
        for r in models_rows
    ]

    # Recent events
    recent = (
        db.query(CostEvent)
        .filter(CostEvent.agent_id == agent.id)
        .order_by(CostEvent.timestamp.desc())
        .limit(50)
        .all()
    )
    recent_events = [EventOut.model_validate(e) for e in recent]

    detail = AgentDetail.model_validate(agent)
    detail.total_cost = float(stats.total_cost)
    detail.request_count = int(stats.total_requests)
    detail.total_tokens = int(stats.total_tokens)
    detail.models_used = models_used
    detail.recent_events = recent_events
    return detail


@router.put("/{agent_id}", response_model=AgentOut)
def update_agent(agent_id: int, body: AgentUpdate, db: Session = Depends(get_db)):
    agent = db.query(Agent).filter(Agent.id == agent_id).first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    update_data = body.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(agent, field, value)

    db.commit()
    db.refresh(agent)
    return AgentOut.model_validate(agent)


@router.delete("/{agent_id}", response_model=AgentOut)
def deactivate_agent(agent_id: int, db: Session = Depends(get_db)):
    agent = db.query(Agent).filter(Agent.id == agent_id).first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    agent.is_active = False
    db.commit()
    db.refresh(agent)
    return AgentOut.model_validate(agent)
