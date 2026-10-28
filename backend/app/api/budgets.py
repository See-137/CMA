import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.models.models import Budget
from app.schemas.schemas import (
    BudgetCheckResponse,
    BudgetCreate,
    BudgetOut,
    BudgetUpdate,
    BudgetWithSpend,
    _check_scope_ref,
)
from app.services.budget_checker import _current_spend, precall_check
from app.timeutils import deployment_tz

router = APIRouter(prefix="/budgets", tags=["budgets"])


@router.get("", response_model=list[BudgetWithSpend])
def list_budgets(db: Session = Depends(get_db)):
    budgets = db.query(Budget).filter(Budget.is_active.is_(True)).all()
    result = []
    tz = deployment_tz(db)
    for b in budgets:
        spend = _current_spend(b, db, tz)
        pct = (spend / b.limit_amount * 100) if b.limit_amount > 0 else 0.0
        item = BudgetWithSpend.model_validate(b)
        item.current_spend = round(spend, 6)
        item.percentage = round(pct, 2)
        result.append(item)
    return result


@router.get("/check", response_model=BudgetCheckResponse)
def check_budget(
    agent_name: str | None = None,
    workflow: str | None = None,
    swarm: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    db: Session = Depends(get_db),
):
    """Pre-call enforcement: may a call with this attribution proceed?

    Returns allowed=false with the exhausted blocking budgets (any
    control_action other than "alert") whose scope matches. Global budgets
    always match; agent/swarm/workflow/provider/model budgets match only when
    that attribute is supplied (a legacy scoped row with an empty scope_ref
    behaves as global, exactly as its spend is summed). The SDK calls this before each LLM call when
    enforce_budgets=True.
    """
    violations = precall_check(db, agent_name, workflow, swarm, provider, model)
    return BudgetCheckResponse(allowed=not violations, violations=violations)


@router.post("", response_model=BudgetOut, status_code=201)
def create_budget(body: BudgetCreate, db: Session = Depends(get_db)):
    budget = Budget(
        name=body.name,
        scope=body.scope,
        scope_ref=body.scope_ref,
        period=body.period,
        limit_amount=body.limit_amount,
        alert_thresholds=json.dumps(body.alert_thresholds),
        control_action=body.control_action,
    )
    db.add(budget)
    db.commit()
    db.refresh(budget)
    return BudgetOut.model_validate(budget)


@router.put("/{budget_id}", response_model=BudgetOut)
def update_budget(budget_id: int, body: BudgetUpdate, db: Session = Depends(get_db)):
    budget = db.query(Budget).filter(Budget.id == budget_id).first()
    if not budget:
        raise HTTPException(status_code=404, detail="Budget not found")

    update_data = body.model_dump(exclude_unset=True)
    if (
        "alert_thresholds" in update_data
        and update_data["alert_thresholds"] is not None
    ):
        update_data["alert_thresholds"] = json.dumps(update_data["alert_thresholds"])

    try:
        _check_scope_ref(
            update_data.get("scope", budget.scope),
            update_data.get("scope_ref", budget.scope_ref),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    for field, value in update_data.items():
        setattr(budget, field, value)

    db.commit()
    db.refresh(budget)
    return BudgetOut.model_validate(budget)


@router.delete("/{budget_id}", response_model=BudgetOut)
def delete_budget(budget_id: int, db: Session = Depends(get_db)):
    budget = db.query(Budget).filter(Budget.id == budget_id).first()
    if not budget:
        raise HTTPException(status_code=404, detail="Budget not found")

    budget.is_active = False
    db.commit()
    db.refresh(budget)
    return BudgetOut.model_validate(budget)
