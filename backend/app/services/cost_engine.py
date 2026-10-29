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

A match found only by rule (d) is stored with ``cost_source='estimated'``, as
is a call whose prompt-cache tokens hit a cache price the model does not set
(those tokens are then priced at the input price; see ``cost_for``).
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


def _price(value: float | None, fallback: float) -> float:
    return fallback if value is None else value


def cost_for(
    pricing: ModelPricing,
    tokens_input: int,
    tokens_output: int,
    cache_read: int = 0,
    cache_write: int = 0,
    cache_write_1h: int = 0,
) -> float:
    """Cost of one call. ``tokens_input`` is ALL input tokens; the cache
    counts are subsets of it (``cache_write_1h`` a subset of ``cache_write``).

    Uncached input at the input price, cache reads at the cache-read price,
    5-minute writes at the cache-write price, 1-hour writes at the 1-hour
    price (falling back to the 5-minute write price). A missing cache price
    falls back to the input price — see ``cache_prices_exact``."""
    cache_read = max(cache_read or 0, 0)
    cache_write = max(cache_write or 0, 0)
    cache_write_1h = min(max(cache_write_1h or 0, 0), cache_write)
    uncached = max(tokens_input - cache_read - cache_write, 0)
    inp = pricing.input_price_per_million
    write_5m = _price(pricing.cache_write_price_per_million, inp)
    write_1h = _price(
        pricing.cache_write_1h_price_per_million,
        write_5m,
    )
    cost = (
        uncached * inp
        + cache_read * _price(pricing.cache_read_price_per_million, inp)
        + (cache_write - cache_write_1h) * write_5m
        + cache_write_1h * write_1h
        + tokens_output * pricing.output_price_per_million
    ) / 1_000_000
    return round(cost, 8)


def cache_prices_exact(
    pricing: ModelPricing,
    cache_read: int = 0,
    cache_write: int = 0,
    cache_write_1h: int = 0,
) -> bool:
    """False when the call used cache tokens whose price is not set on the
    model, so ``cost_for`` fell back to another price (cost is an estimate).
    Counts are clamped exactly as ``cost_for`` clamps them."""
    cache_read = max(cache_read or 0, 0)
    cache_write = max(cache_write or 0, 0)
    cache_write_1h = min(max(cache_write_1h or 0, 0), cache_write)
    if cache_read and pricing.cache_read_price_per_million is None:
        return False
    if cache_write_1h and pricing.cache_write_1h_price_per_million is None:
        return False
    writes_5m = cache_write - cache_write_1h
    return not (writes_5m > 0 and pricing.cache_write_price_per_million is None)


def event_cost(
    pricing: ModelPricing,
    exact_model: bool,
    tokens_input: int,
    tokens_output: int,
    cache_read: int = 0,
    cache_write: int = 0,
    cache_write_1h: int = 0,
) -> tuple[float, str]:
    """(cost, cost_source) for a priced event: 'pricing' when both the model
    match and every price it used are exact, else 'estimated'."""
    cost = cost_for(
        pricing, tokens_input, tokens_output, cache_read, cache_write, cache_write_1h
    )
    exact = exact_model and cache_prices_exact(
        pricing, cache_read, cache_write, cache_write_1h
    )
    return cost, "pricing" if exact else "estimated"


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

    from app.services.rollup import compute_daily_rollups

    today = utcnow().date()
    rollup_dates: list[date] = []
    archived_dates: list[date] = []
    recomputed: set[date] = set()
    batch_dates: set[date] = set()

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
            new_cost, source = event_cost(
                pricing,
                exact,
                event.tokens_input,
                event.tokens_output,
                event.tokens_cache_read or 0,
                event.tokens_cache_write or 0,
                event.tokens_cache_write_1h or 0,
            )
            added_cost += new_cost - (event.cost or 0.0)
            repriced_ids.append(event.id)
            batch_dates.add(event.timestamp.date())
            if not dry_run:
                event.cost = new_cost
                event.cost_source = source
        if not dry_run:
            db.commit()
            # Recompute this batch's completed days right away, so an
            # interrupted reprice never leaves committed events newer than
            # their rollups (a re-run only sees the remaining events).
            # Every batch re-rolls every completed day it touched (idempotent):
            # a day whose events span several batches must be recomputed
            # after its LAST batch, not only its first.
            for d in sorted(batch_dates):
                if d >= today:
                    continue
                if compute_daily_rollups(db, d):
                    if d not in recomputed:
                        rollup_dates.append(d)
                elif d not in recomputed:
                    # Archived days (raw events already pruned) are skipped;
                    # retention adds their late events later.
                    archived_dates.append(d)
                recomputed.add(d)
        else:
            db.expunge_all()  # keep memory flat; nothing was changed
        batch_dates = set()

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
