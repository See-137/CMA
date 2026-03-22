import logging

from sqlalchemy.orm import Session

from app.models.models import ModelPricing, Provider

logger = logging.getLogger(__name__)


def calculate_cost(
    provider_name: str,
    model_name: str,
    tokens_input: int,
    tokens_output: int,
    db: Session,
) -> float:
    """Look up pricing and compute cost for the given model/provider/token counts."""
    pricing = (
        db.query(ModelPricing)
        .join(Provider, Provider.id == ModelPricing.provider_id)
        .filter(
            Provider.name == provider_name,
            ModelPricing.model_name == model_name,
            ModelPricing.is_active.is_(True),
            Provider.is_active.is_(True),
        )
        .first()
    )

    if pricing is None:
        logger.warning(
            "No pricing found for provider=%s model=%s — returning 0.0",
            provider_name,
            model_name,
        )
        return 0.0

    cost = (tokens_input / 1_000_000 * pricing.input_price_per_million) + (
        tokens_output / 1_000_000 * pricing.output_price_per_million
    )
    return round(cost, 8)
