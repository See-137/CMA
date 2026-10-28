"""Price lookup and cost calculation.

Pricing resolution (how an event's provider/model strings find a price row):

1. Provider: case-insensitive match on ``Provider.name`` *or*
   ``Provider.provider_type`` (active providers only). The SDK reports
   lowercase vendor ids ("openai", "anthropic") while the seeded rows are
   title-case names ("OpenAI") — both must price. When several providers
   match, an exact name beats a case-insensitive name beats a type match.
2. Model, first rule that matches wins:
   a. exact ``model_name``
   b. one of the comma-separated ``model_alias`` values (case-insensitive)
   c. case-insensitive ``model_name``
   d. the model string with a trailing date-snapshot suffix removed
      (``gpt-4o-2024-08-06`` -> ``gpt-4o``, ``claude-x-20241022`` -> ``claude-x``)
      compared against names and aliases (case-insensitive)

No match prices the event at 0.0 (logged as a warning).
"""

import logging
import re

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.models.models import ModelPricing, Provider

logger = logging.getLogger(__name__)

_DATE_SUFFIX = re.compile(r"-(\d{4}-\d{2}-\d{2}|\d{8})$")


def _aliases(pricing: ModelPricing) -> set[str]:
    if not pricing.model_alias:
        return set()
    return {a.strip().lower() for a in pricing.model_alias.split(",") if a.strip()}


def resolve_pricing(
    provider_name: str, model_name: str, db: Session
) -> ModelPricing | None:
    """Find the active price row for an event's provider/model strings."""
    wanted_provider = provider_name.strip().lower()
    rows = (
        db.query(ModelPricing, Provider)
        .join(Provider, Provider.id == ModelPricing.provider_id)
        .filter(
            ModelPricing.is_active.is_(True),
            Provider.is_active.is_(True),
            or_(
                func.lower(Provider.name) == wanted_provider,
                func.lower(Provider.provider_type) == wanted_provider,
            ),
        )
        .all()
    )
    if not rows:
        return None

    def provider_rank(provider: Provider) -> int:
        if provider.name == provider_name:
            return 0
        if provider.name.lower() == wanted_provider:
            return 1
        return 2

    model = model_name.strip()
    model_lower = model.lower()
    stripped = _DATE_SUFFIX.sub("", model_lower)
    matchers = [
        lambda p: p.model_name == model,
        lambda p: model_lower in _aliases(p),
        lambda p: p.model_name.lower() == model_lower,
        lambda p: (
            stripped != model_lower
            and (p.model_name.lower() == stripped or stripped in _aliases(p))
        ),
    ]
    for matches in matchers:
        hits = [(pricing, prov) for pricing, prov in rows if matches(pricing)]
        if hits:
            pricing, _ = min(hits, key=lambda h: (provider_rank(h[1]), h[0].id))
            return pricing
    return None


def cost_for(pricing: ModelPricing, tokens_input: int, tokens_output: int) -> float:
    cost = (tokens_input / 1_000_000 * pricing.input_price_per_million) + (
        tokens_output / 1_000_000 * pricing.output_price_per_million
    )
    return round(cost, 8)


def calculate_cost(
    provider_name: str,
    model_name: str,
    tokens_input: int,
    tokens_output: int,
    db: Session,
) -> float:
    """Look up pricing and compute cost for the given model/provider/token counts."""
    pricing = resolve_pricing(provider_name, model_name, db)
    if pricing is None:
        logger.warning(
            "No pricing found for provider=%s model=%s — returning 0.0",
            provider_name,
            model_name,
        )
        return 0.0
    return cost_for(pricing, tokens_input, tokens_output)
