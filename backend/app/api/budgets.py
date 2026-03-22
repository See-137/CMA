import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.models.models import Budget
from app.schemas.schemas import BudgetCreate, BudgetOut, BudgetUpdate, BudgetWithSpend
from app.services.budget_checker import _current_spend

router = APIRouter(prefix="/budgets", tags=["budgets"])


@router.get("", response_model=list[BudgetWithSpend])
def list_budgets(db: Session = Depends(get_db)):
    budgets = db.query(Budget).filter(Budget.is_active.is_(True)).all()
    result = []
    for b in budgets:
        spend = _current_spend(b, db)
        pct = (spend / b.limit_amount * 100) if b.limit_amount > 0 else 0.0
        item = BudgetWithSpend.model_validate(b)
        item.current_spend = round(spend, 6)
        item.percentage = round(pct, 2)
        result.append(item)
    return result


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
