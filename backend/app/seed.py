import logging

from sqlalchemy.orm import Session

from app.models.models import ModelPricing, Provider

logger = logging.getLogger(__name__)

# When the prices below were last checked against the vendors' official pages.
# They change without notice: operators edit them under Providers, then use
# Settings -> Reprice. Keep alembic 007 in sync when adding a release's models
# (a test compares them): this list only reaches a fresh install or a full reset.
PRICES_VERIFIED_ON = "2026-11-04"

# Per 1M tokens, standard tier. "cache" = (read, short write, long write):
# Anthropic 5-minute / 1-hour, OpenAI 30-minute / 12-hour.
# "long" = (threshold, input+cache multiplier, output multiplier): a prompt with
# MORE than `threshold` input tokens is billed at the higher rate for the whole
# request. Sources:
#   platform.claude.com/docs/en/about-claude/pricing
#   developers.openai.com/api/docs/pricing and /docs/models/<model>
#   aistudio.google.com/docs/pricing
# Not modelled: Gemini's hourly context-cache storage charge (ACC-25). Announced,
# not modelled here: gemini-4-argon doubles on 2026-12-01; gemini-3.8
# and 3.6 flash double on 2027-01-01 (ACC-24).
_OPENAI_LONG = (272_000, 2.0, 1.5)

SEED_DATA = [
    {
        "name": "OpenAI",
        "provider_type": "openai",
        "models": [
            {
                "model_name": "gpt-6-astra",
                "input": 10.00,
                "output": 50.00,
                "cache": (1.00, 12.50, 25.00),
                "long": _OPENAI_LONG,
            },
            {
                "model_name": "gpt-6.1-sol",
                "input": 2.00,
                "output": 10.00,
                "cache": (0.10, 2.50, 5.00),
                "long": _OPENAI_LONG,
            },
            {
                "model_name": "gpt-6.1-terra",
                "input": 1.00,
                "output": 5.00,
                "cache": (0.05, 1.25, 2.50),
                "long": _OPENAI_LONG,
            },
            {
                "model_name": "gpt-6-luna",
                "input": 0.10,
                "output": 0.50,
                "cache": (0.01, 0.125, 0.25),
                "long": _OPENAI_LONG,
            },
            {
                "model_name": "chat-latest",
                "input": 5.00,
                "output": 30.00,
                "cache": (0.50, None, None),
            },
            {
                "model_name": "gpt-5.3-codex",
                "input": 1.75,
                "output": 14.00,
                "cache": (0.175, None, None),
            },
            {
                "model_name": "gpt-5.6-sol",
                "alias": "gpt-5.6",
                "input": 4.00,
                "output": 20.00,
                "cache": (0.40, 5.00, 10.00),
                "long": _OPENAI_LONG,
            },
            {
                "model_name": "gpt-5.6-cyber",
                "input": 12.50,
                "output": 75.00,
                "cache": (1.25, 15.625, 31.25),
            },
            {
                "model_name": "gpt-6-rosalind",
                "input": 15.00,
                "output": 75.00,
                "cache": (1.50, 18.75, 37.50),
            },
            # Legacy (kept for older event streams and test fixtures)
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
            {
                "model_name": "claude-fable-5-1",
                "input": 10.00,
                "output": 50.00,
                "cache": (0.25, 12.50, 20.00),
            },
            {
                "model_name": "claude-opus-5-5",
                "input": 4.00,
                "output": 20.00,
                "cache": (0.20, 5.00, 8.00),
            },
            {
                "model_name": "claude-sonnet-5-5",
                "input": 2.00,
                "output": 10.00,
                "cache": (0.10, 2.50, 4.00),
            },
            {
                "model_name": "claude-haiku-5-5",
                "input": 0.10,
                "output": 0.50,
                "cache": (0.01, 0.125, 0.20),
                "long": (100_000, 5.0, 5.0),
            },
            {
                "model_name": "claude-fable-5",
                "input": 10.00,
                "output": 50.00,
                "cache": (1.00, 12.50, 20.00),
            },
            {
                "model_name": "claude-opus-5",
                "input": 5.00,
                "output": 25.00,
                "cache": (0.50, 6.25, 10.00),
            },
            {
                "model_name": "claude-opus-4-8",
                "input": 5.00,
                "output": 25.00,
                "cache": (0.50, 6.25, 10.00),
            },
            {
                "model_name": "claude-opus-4-7",
                "input": 5.00,
                "output": 25.00,
                "cache": (0.50, 6.25, 10.00),
            },
            {
                "model_name": "claude-opus-4-6",
                "input": 5.00,
                "output": 25.00,
                "cache": (0.50, 6.25, 10.00),
            },
            {
                "model_name": "claude-opus-4-5",
                "alias": "claude-opus-4-5-20251101",
                "input": 5.00,
                "output": 25.00,
                "cache": (0.50, 6.25, 10.00),
            },
            {
                "model_name": "claude-sonnet-5",
                "input": 2.00,
                "output": 10.00,
                "cache": (0.20, 2.50, 4.00),
            },
            {
                "model_name": "claude-sonnet-4-6",
                "input": 3.00,
                "output": 15.00,
                "cache": (0.30, 3.75, 6.00),
            },
            {
                "model_name": "claude-sonnet-4-5",
                "alias": "claude-sonnet-4-5-20250929",
                "input": 3.00,
                "output": 15.00,
                "cache": (0.30, 3.75, 6.00),
            },
            {
                "model_name": "claude-haiku-4-5",
                "alias": "claude-haiku-4-5-20251001",
                "input": 1.00,
                "output": 5.00,
                "cache": (0.10, 1.25, 2.00),
            },
            {
                "model_name": "claude-mythos-5-1",
                "input": 10.00,
                "output": 50.00,
                "cache": (0.25, 12.50, 20.00),
            },
            {
                "model_name": "claude-mythos-5",
                "input": 10.00,
                "output": 50.00,
                "cache": (1.00, 12.50, 20.00),
            },
            {
                "model_name": "claude-opus-4-1",
                "alias": "claude-opus-4-1-20250805",
                "input": 15.00,
                "output": 75.00,
                "cache": (1.50, 18.75, 30.00),
            },
            {
                "model_name": "claude-opus-4",
                "alias": "claude-opus-4-20250514",
                "input": 15.00,
                "output": 75.00,
                "cache": (1.50, 18.75, 30.00),
            },
            {
                "model_name": "claude-sonnet-4",
                "alias": "claude-sonnet-4-20250514",
                "input": 3.00,
                "output": 15.00,
                "cache": (0.30, 3.75, 6.00),
            },
            {
                "model_name": "claude-3-5-haiku",
                "alias": "claude-3-5-haiku-20241022",
                "input": 0.80,
                "output": 4.00,
                "cache": (0.08, 1.00, 1.60),
            },
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
            {
                "model_name": "gemini-4-argon",
                "input": 2.00,
                "output": 10.00,
                "cache": (0.10, None, None),
            },
            {
                "model_name": "gemini-3.8-flash",
                "input": 0.75,
                "output": 3.75,
                "cache": (0.075, None, None),
            },
            {
                "model_name": "gemini-3.6-flash",
                "input": 0.75,
                "output": 3.75,
                "cache": (0.075, None, None),
            },
            {
                "model_name": "gemini-3.5-flash-lite",
                "input": 0.30,
                "output": 2.50,
                "cache": (0.03, None, None),
            },
            {
                "model_name": "gemini-3.1-pro-preview",
                "alias": "gemini-3.1-pro-preview-customtools",
                "input": 2.00,
                "output": 12.00,
                "cache": (0.20, None, None),
                "long": (200_000, 2.0, 1.5),
            },
            {
                "model_name": "gemini-3.1-flash-lite",
                "input": 0.25,
                "output": 1.50,
                "cache": (0.025, None, None),
            },
            {
                "model_name": "gemini-3-flash-preview",
                "input": 0.50,
                "output": 3.00,
                "cache": (0.05, None, None),
            },
            {
                "model_name": "gemini-2.5-flash-lite",
                "input": 0.10,
                "output": 0.40,
                "cache": (0.01, None, None),
            },
            {
                "model_name": "gemini-2.5-pro",
                "input": 1.25,
                "output": 10.00,
                "cache": (0.125, None, None),
                "long": (200_000, 2.0, 1.5),
            },
            {
                "model_name": "gemini-2.5-flash",
                "input": 0.30,
                "output": 2.50,
                "cache": (0.03, None, None),
            },
            # Legacy (kept for older event streams and test fixtures)
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
            read, write, write_1h = model_data.get("cache", (None, None, None))
            threshold, long_input, long_output = model_data.get(
                "long", (None, None, None)
            )
            model = ModelPricing(
                provider_id=provider.id,
                model_name=model_data["model_name"],
                model_alias=model_data.get("alias"),
                input_price_per_million=model_data["input"],
                output_price_per_million=model_data["output"],
                cache_read_price_per_million=read,
                cache_write_price_per_million=write,
                cache_write_1h_price_per_million=write_1h,
                long_context_threshold=threshold,
                long_context_input_multiplier=long_input,
                long_context_output_multiplier=long_output,
            )
            db.add(model)

    db.commit()
    logger.info("Seed complete: %d providers created", len(SEED_DATA))
