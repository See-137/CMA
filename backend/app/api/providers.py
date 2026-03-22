from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.models.models import ModelPricing, Provider
from app.schemas.schemas import (
    ModelPricingIn,
    ModelPricingOut,
    ModelPricingUpdate,
    ProviderCreate,
    ProviderOut,
    ProviderUpdate,
)

router = APIRouter(tags=["providers"])


# ── Providers ──────────────────────────────────────────────────────────────────


@router.get("/providers", response_model=list[ProviderOut])
def list_providers(db: Session = Depends(get_db)):
    providers = db.query(Provider).all()
    return [ProviderOut.model_validate(p) for p in providers]


@router.post("/providers", response_model=ProviderOut, status_code=201)
def create_provider(body: ProviderCreate, db: Session = Depends(get_db)):
    existing = db.query(Provider).filter(Provider.name == body.name).first()
    if existing:
        raise HTTPException(
            status_code=409, detail="Provider with this name already exists"
        )

    provider = Provider(name=body.name, provider_type=body.provider_type)
    db.add(provider)
    db.flush()

    for m in body.models:
        model = ModelPricing(
            provider_id=provider.id,
            model_name=m.model_name,
            model_alias=m.model_alias,
            input_price_per_million=m.input_price_per_million,
            output_price_per_million=m.output_price_per_million,
        )
        db.add(model)

    db.commit()
    db.refresh(provider)
    return ProviderOut.model_validate(provider)


@router.get("/providers/{provider_id}", response_model=ProviderOut)
def get_provider(provider_id: int, db: Session = Depends(get_db)):
    provider = db.query(Provider).filter(Provider.id == provider_id).first()
    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")
    return ProviderOut.model_validate(provider)


@router.put("/providers/{provider_id}", response_model=ProviderOut)
def update_provider(
    provider_id: int, body: ProviderUpdate, db: Session = Depends(get_db)
):
    provider = db.query(Provider).filter(Provider.id == provider_id).first()
    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")

    update_data = body.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(provider, field, value)

    db.commit()
    db.refresh(provider)
    return ProviderOut.model_validate(provider)


# ── Models ─────────────────────────────────────────────────────────────────────


@router.get("/models", response_model=list[ModelPricingOut])
def list_all_models(db: Session = Depends(get_db)):
    models = db.query(ModelPricing).all()
    return [ModelPricingOut.model_validate(m) for m in models]


@router.post(
    "/providers/{provider_id}/models",
    response_model=ModelPricingOut,
    status_code=201,
)
def create_model(provider_id: int, body: ModelPricingIn, db: Session = Depends(get_db)):
    provider = db.query(Provider).filter(Provider.id == provider_id).first()
    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")

    existing = (
        db.query(ModelPricing)
        .filter(
            ModelPricing.provider_id == provider_id,
            ModelPricing.model_name == body.model_name,
        )
        .first()
    )
    if existing:
        raise HTTPException(
            status_code=409,
            detail="Model with this name already exists for this provider",
        )

    model = ModelPricing(
        provider_id=provider_id,
        model_name=body.model_name,
        model_alias=body.model_alias,
        input_price_per_million=body.input_price_per_million,
        output_price_per_million=body.output_price_per_million,
    )
    db.add(model)
    db.commit()
    db.refresh(model)
    return ModelPricingOut.model_validate(model)


@router.put("/models/{model_id}", response_model=ModelPricingOut)
def update_model(
    model_id: int, body: ModelPricingUpdate, db: Session = Depends(get_db)
):
    model = db.query(ModelPricing).filter(ModelPricing.id == model_id).first()
    if not model:
        raise HTTPException(status_code=404, detail="Model not found")

    update_data = body.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(model, field, value)

    db.commit()
    db.refresh(model)
    return ModelPricingOut.model_validate(model)


@router.delete("/models/{model_id}", response_model=ModelPricingOut)
def delete_model(model_id: int, db: Session = Depends(get_db)):
    model = db.query(ModelPricing).filter(ModelPricing.id == model_id).first()
    if not model:
        raise HTTPException(status_code=404, detail="Model not found")

    model.is_active = False
    db.commit()
    db.refresh(model)
    return ModelPricingOut.model_validate(model)
