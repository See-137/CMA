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

A match found only by rule (d) is stored with ``cost_source='estimated'``.
No match prices the event at 0.0 and marks it ``unpriced`` (``cost_source``)
so it stays visible and can be repriced once pricing exists
(``reprice_events`` / ``POST /api/v1/admin/reprice``).
"""

import logging
import re
from datetime import date, datetime

from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session

from app.models.models import CostEvent, ModelPricing, Provider
from app.timeutils import utcnow

logger = logging.getLogger(__name__)

_REPRICE_BATCH = 1000
_DATE_SUFFIX = re.compile(r"-(\d{4}-\d{2}-\d{2}|\d{8})$")


def _aliases(pricing: ModelPricing) -> set[str]:
    if not pricing.model_alias:
        return set()
    return {a.strip().lower() for a in pricing.model_alias.split(",") if a.strip()}


def resolve_pricing(
    provider_name: str, model_name: str, db: Session
) -> ModelPricing | None:
    """Find the active price row for an event's provider/model strings."""
    match = resolve_pricing_match(provider_name, model_name, db)
    return match[0] if match else None


def resolve_pricing_match(
    provider_name: str, model_name: str, db: Session
) -> tuple[ModelPricing, bool] | None:
    """(price row, exact) — ``exact`` is False when only the date-snapshot
    fallback matched: a dated snapshot can be priced differently from its
    alias (e.g. older snapshots cost more), so such costs are estimates."""
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
    for rule, matches in enumerate(matchers):
        hits = [(pricing, prov) for pricing, prov in rows if matches(pricing)]
        if hits:
            # Ties between same-type providers: the earliest-created provider.
            pricing, _ = min(
                hits, key=lambda h: (provider_rank(h[1]), h[1].id, h[0].id)
            )
            return pricing, rule < 3
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


def unpriced_clause():
    """SQL predicate for events stored without a price: marked ``unpriced``,
    or legacy rows (no ``cost_source``) at 0.0 with non-zero tokens."""
    return or_(
        CostEvent.cost_source == "unpriced",
        and_(
            CostEvent.cost_source.is_(None),
            CostEvent.cost == 0,
            (CostEvent.tokens_input + CostEvent.tokens_output) > 0,
        ),
    )


def reprice_events(
    db: Session,
    from_date: datetime | None = None,
    to_date: datetime | None = None,
    dry_run: bool = False,
) -> dict:
    """Recompute cost for events stored without a price.

    Candidates are events marked ``unpriced`` plus legacy rows (``cost_source``
    NULL) stored at 0.0 with non-zero tokens — exactly what the old exact-match
    lookup produced. Client-supplied costs are never touched. Daily rollups for
    affected *completed* days are recomputed so dashboards and Scrooge agree
    with the corrected events.
    """
    query = db.query(CostEvent).filter(unpriced_clause())
    if from_date is not None:
        query = query.filter(CostEvent.timestamp >= from_date)
    if to_date is not None:
        query = query.filter(CostEvent.timestamp <= to_date)

    cache: dict[tuple[str, str], tuple[ModelPricing, bool] | None] = {}
    repriced_ids: list[int] = []
    still_unpriced: dict[str, int] = {}
    added_cost = 0.0
    dates: set[date] = set()

    # Keyset batches, committed one at a time: a very large reprice makes
    # durable progress (re-running continues where it stopped) and never
    # holds every candidate row in memory.
    last_id = 0
    while True:
        batch = (
            query.filter(CostEvent.id > last_id)
            .order_by(CostEvent.id)
            .limit(_REPRICE_BATCH)
            .all()
        )
        if not batch:
            break
        last_id = batch[-1].id
        for event in batch:
            key = (event.provider_name, event.model_name)
            if key not in cache:
                cache[key] = resolve_pricing_match(
                    event.provider_name, event.model_name, db
                )
            match = cache[key]
            if match is None:
                label = f"{event.provider_name}/{event.model_name}"
                still_unpriced[label] = still_unpriced.get(label, 0) + 1
                if not dry_run:
                    event.cost_source = "unpriced"
                continue
            pricing, exact = match
            new_cost = cost_for(pricing, event.tokens_input, event.tokens_output)
            added_cost += new_cost - (event.cost or 0.0)
            repriced_ids.append(event.id)
            dates.add(event.timestamp.date())
            if not dry_run:
                event.cost = new_cost
                event.cost_source = "pricing" if exact else "estimated"
        if not dry_run:
            db.commit()
        else:
            db.expunge_all()  # keep memory flat; nothing was changed

    rollup_dates: list[date] = []
    archived_dates: list[date] = []
    if not dry_run:
        from app.services.rollup import compute_daily_rollups

        today = utcnow().date()
        for d in sorted(dates):
            # Archived days (raw events already pruned) are skipped by
            # compute_daily_rollups; retention adds their late events later.
            if d >= today:
                continue
            if compute_daily_rollups(db, d):
                rollup_dates.append(d)
            else:
                archived_dates.append(d)

    return {
        "dry_run": dry_run,
        "repriced": len(repriced_ids),
        "added_cost": round(added_cost, 8),
        "still_unpriced": still_unpriced,
        "rollup_dates_recomputed": [d.isoformat() for d in rollup_dates],
        # Days whose raw events were already pruned: their archived rollups
        # keep the old (often \$0) cost; the repriced late events are added
        # to them at the next retention run.
        "archived_dates_not_recomputed": [d.isoformat() for d in archived_dates],
        "repriced_ids": repriced_ids,
    }
