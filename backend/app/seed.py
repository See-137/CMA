import logging

from sqlalchemy.orm import Session

from app.models.models import ModelPricing, Provider

logger = logging.getLogger(__name__)

SEED_DATA = [
    {
        "name": "OpenAI",
        "provider_type": "openai",
        "models": [
            {"model_name": "gpt-4o", "input": 2.50, "output": 10.00},
            {"model_name": "gpt-4-turbo", "input": 10.00, "output": 30.00},
            {"model_name": "gpt-4", "input": 30.00, "output": 60.00},
            {"model_name": "gpt-3.5-turbo", "input": 0.50, "output": 1.50},
        ],
    },
    {
        "name": "Anthropic",
        "provider_type": "anthropic",
        "models": [
            # Current generation (prices per 1M tokens, standard tier)
            {"model_name": "claude-opus-4-8", "input": 5.00, "output": 25.00},
            {"model_name": "claude-opus-4-7", "input": 5.00, "output": 25.00},
            {"model_name": "claude-sonnet-5", "input": 3.00, "output": 15.00},
            {"model_name": "claude-sonnet-4-6", "input": 3.00, "output": 15.00},
            {"model_name": "claude-haiku-4-5", "input": 1.00, "output": 5.00},
            # Legacy (kept for older event streams and test fixtures)
            {"model_name": "claude-3.5-sonnet", "input": 3.00, "output": 15.00},
            {"model_name": "claude-3-opus", "input": 15.00, "output": 75.00},
            {"model_name": "claude-3-sonnet", "input": 3.00, "output": 15.00},
            {"model_name": "claude-3-haiku", "input": 0.25, "output": 1.25},
        ],
    },
    {
        "name": "Google",
        "provider_type": "google",
        "models": [
            {"model_name": "gemini-1.5-pro", "input": 1.25, "output": 5.00},
            {"model_name": "gemini-1.5-flash", "input": 0.075, "output": 0.30},
        ],
    },
]


def seed_providers(db: Session) -> None:
    """Populate default providers and model pricing if the providers table is empty."""
    existing = db.query(Provider).count()
    if existing > 0:
        logger.info(
            "Providers table already populated (%d rows) — skipping seed", existing
        )
        return

    logger.info("Seeding default providers and model pricing...")
    for provider_data in SEED_DATA:
        provider = Provider(
            name=provider_data["name"],
            provider_type=provider_data["provider_type"],
        )
        db.add(provider)
        db.flush()  # get provider.id

        for model_data in provider_data["models"]:
            model = ModelPricing(
                provider_id=provider.id,
                model_name=model_data["model_name"],
                input_price_per_million=model_data["input"],
                output_price_per_million=model_data["output"],
            )
            db.add(model)

    db.commit()
    logger.info("Seed complete: %d providers created", len(SEED_DATA))
