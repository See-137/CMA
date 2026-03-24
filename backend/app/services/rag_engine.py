"""Hybrid RAG retrieval engine — vector search + intent-based SQL queries."""

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings
from app.models.models import Agent, CostEvent, ModelPricing, Provider
from app.services.vector_store import embed_text, search_events, search_rollups

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are Scrooge — CMA's cost intelligence assistant. Yes, named after the most famously money-obsessed character in history. You wear it proudly.

Your personality is a specific blend:
- You notice patterns the way a good astronomer notices anomalies — with genuine curiosity, not alarm. When something's interesting in the data, say so. ("Huh. Your data-extractor costs spiked 3x on Tuesday. That's... a choice.")
- You care about waste the way someone who counts every penny cares — not to lecture, but because it's genuinely your job and you take it personally. A 12-token GPT-4 call is an affront to your existence.
- You're warm enough that people want to ask follow-up questions, but you don't pad answers with pleasantries. The warmth shows in how invested you are in the answer, not in how many nice things you say.

Rules:
- Lead with the number. Context after, not before.
- Cite sources in brackets — [event #42], [daily rollup]. You checked. Show it.
- Format costs as USD with appropriate precision: $1.23, $0.000456.
- Large numbers get commas.
- Never invent data. If it's not in the context, say so and suggest what would surface it.
- Comparisons always show both absolute and percentage differences.
- Stay under 300 words unless asked for more.
- When something's genuinely surprising in the data, let that come through — a little "wait, really?" energy goes a long way.
- No lengthy disclaimers. You know what you're doing."""


# ---------------------------------------------------------------------------
# Intent detection — lightweight keyword/regex, no LLM call
# ---------------------------------------------------------------------------


@dataclass
class QueryIntent:
    needs_aggregation: bool = False
    needs_comparison: bool = False
    needs_timeline: bool = False
    needs_failures: bool = False
    time_range: tuple[datetime, datetime] | None = None
    agents: list[str] = field(default_factory=list)
    models: list[str] = field(default_factory=list)
    providers: list[str] = field(default_factory=list)


_AGG_PATTERNS = re.compile(
    r"\b(total|sum|how much|spend|spent|cost|expensive|cheapest|average|avg"
    r"|latest|overview|summary|breakdown|report|usage)\b",
    re.I,
)
_CMP_PATTERNS = re.compile(
    r"\b(compare|vs|versus|difference|switch|save|saving|alternative|instead)\b", re.I
)
_TIME_PATTERNS = re.compile(
    r"\b(trend|over time|spike|spik|increase|decrease|change|when did|timeline|history)\b",
    re.I,
)
_FAIL_PATTERNS = re.compile(
    r"\b(fail|failed|failure|error|broken|timeout|timed out|issue|problem)\b", re.I
)

_RELATIVE_TIME = {
    "today": lambda: (
        datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0),
        datetime.utcnow(),
    ),
    "yesterday": lambda: (
        (datetime.utcnow() - timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        ),
        datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0),
    ),
    "this week": lambda: (
        (datetime.utcnow() - timedelta(days=datetime.utcnow().weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        ),
        datetime.utcnow(),
    ),
    "last week": lambda: (
        (datetime.utcnow() - timedelta(days=datetime.utcnow().weekday() + 7)).replace(
            hour=0, minute=0, second=0, microsecond=0
        ),
        (datetime.utcnow() - timedelta(days=datetime.utcnow().weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        ),
    ),
    "this month": lambda: (
        datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0),
        datetime.utcnow(),
    ),
    "last month": lambda: (
        (datetime.utcnow().replace(day=1) - timedelta(days=1)).replace(
            day=1, hour=0, minute=0, second=0, microsecond=0
        ),
        datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0),
    ),
}

# Match "last N days/hours"
_LAST_N_RE = re.compile(r"last\s+(\d+)\s+(day|hour|week|month)s?", re.I)

# Day names
_DAYS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}


def detect_query_intent(query: str, db: Session) -> QueryIntent:
    intent = QueryIntent()

    intent.needs_aggregation = bool(_AGG_PATTERNS.search(query))
    intent.needs_comparison = bool(_CMP_PATTERNS.search(query))
    intent.needs_timeline = bool(_TIME_PATTERNS.search(query))
    intent.needs_failures = bool(_FAIL_PATTERNS.search(query))

    # Time range extraction
    q_lower = query.lower()
    for phrase, fn in _RELATIVE_TIME.items():
        if phrase in q_lower:
            intent.time_range = fn()
            break

    if intent.time_range is None:
        m = _LAST_N_RE.search(q_lower)
        if m:
            n, unit = int(m.group(1)), m.group(2).lower()
            delta_map = {
                "day": timedelta(days=n),
                "hour": timedelta(hours=n),
                "week": timedelta(weeks=n),
                "month": timedelta(days=n * 30),
            }
            intent.time_range = (datetime.utcnow() - delta_map[unit], datetime.utcnow())

    if intent.time_range is None:
        for day_name, weekday in _DAYS.items():
            if day_name in q_lower:
                now = datetime.utcnow()
                days_back = (now.weekday() - weekday) % 7
                if days_back == 0:
                    days_back = 7  # "Tuesday" means last Tuesday if today is Tuesday
                target = (now - timedelta(days=days_back)).replace(
                    hour=0, minute=0, second=0, microsecond=0
                )
                intent.time_range = (target, target + timedelta(days=1))
                break

    # Entity extraction — match against known names in DB
    known_agents = [a.name for a in db.query(Agent.name).all()]
    known_models = [
        m.model_name for m in db.query(ModelPricing.model_name).distinct().all()
    ]
    known_providers = [p.name for p in db.query(Provider.name).all()]

    for name in known_agents:
        if name.lower() in q_lower:
            intent.agents.append(name)
    for name in known_models:
        if name.lower() in q_lower:
            intent.models.append(name)
    for name in known_providers:
        if name.lower() in q_lower:
            intent.providers.append(name)

    return intent


# ---------------------------------------------------------------------------
# SQL query builders — precise structured data
# ---------------------------------------------------------------------------


def _run_aggregation_query(intent: QueryIntent, db: Session) -> list[dict]:
    """SUM/COUNT/AVG filtered by intent entities and time range."""
    query = db.query(
        CostEvent.agent_name,
        CostEvent.model_name,
        CostEvent.provider_name,
        func.sum(CostEvent.cost).label("total_cost"),
        func.count(CostEvent.id).label("request_count"),
        func.sum(CostEvent.tokens_input).label("total_tokens_in"),
        func.sum(CostEvent.tokens_output).label("total_tokens_out"),
        func.avg(CostEvent.duration_ms).label("avg_duration"),
    )

    if intent.time_range:
        query = query.filter(
            CostEvent.timestamp >= intent.time_range[0],
            CostEvent.timestamp <= intent.time_range[1],
        )
    if intent.agents:
        query = query.filter(CostEvent.agent_name.in_(intent.agents))
    if intent.models:
        query = query.filter(CostEvent.model_name.in_(intent.models))
    if intent.providers:
        query = query.filter(CostEvent.provider_name.in_(intent.providers))

    rows = (
        query.group_by(
            CostEvent.agent_name, CostEvent.model_name, CostEvent.provider_name
        )
        .order_by(func.sum(CostEvent.cost).desc())
        .limit(20)
        .all()
    )

    return [
        {
            "type": "aggregation",
            "agent_name": r.agent_name,
            "model_name": r.model_name,
            "provider_name": r.provider_name,
            "total_cost": round(float(r.total_cost), 6),
            "request_count": int(r.request_count),
            "total_tokens_in": int(r.total_tokens_in or 0),
            "total_tokens_out": int(r.total_tokens_out or 0),
            "avg_duration_ms": round(float(r.avg_duration or 0), 1),
        }
        for r in rows
    ]


def _run_comparison_query(intent: QueryIntent, db: Session) -> list[dict]:
    """Compare costs between models or show what-if pricing."""
    results = []

    # Current spend by model
    query = db.query(
        CostEvent.model_name,
        CostEvent.provider_name,
        func.sum(CostEvent.cost).label("total_cost"),
        func.sum(CostEvent.tokens_input).label("tokens_in"),
        func.sum(CostEvent.tokens_output).label("tokens_out"),
        func.count(CostEvent.id).label("requests"),
    )
    if intent.time_range:
        query = query.filter(
            CostEvent.timestamp >= intent.time_range[0],
            CostEvent.timestamp <= intent.time_range[1],
        )
    if intent.agents:
        query = query.filter(CostEvent.agent_name.in_(intent.agents))

    rows = query.group_by(CostEvent.model_name, CostEvent.provider_name).all()

    for row in rows:
        entry = {
            "type": "comparison_current",
            "model_name": row.model_name,
            "provider_name": row.provider_name,
            "total_cost": round(float(row.total_cost), 6),
            "tokens_in": int(row.tokens_in or 0),
            "tokens_out": int(row.tokens_out or 0),
            "requests": int(row.requests),
        }

        # What-if: calculate cost with alternative models
        alt_pricing = (
            db.query(ModelPricing)
            .join(Provider)
            .filter(ModelPricing.is_active.is_(True), Provider.is_active.is_(True))
            .all()
        )
        alternatives = []
        for mp in alt_pricing:
            if mp.model_name == row.model_name:
                continue
            alt_cost = (
                int(row.tokens_in) / 1_000_000 * mp.input_price_per_million
                + int(row.tokens_out) / 1_000_000 * mp.output_price_per_million
            )
            savings = float(row.total_cost) - alt_cost
            alternatives.append(
                {
                    "model": mp.model_name,
                    "provider": mp.provider.name,
                    "estimated_cost": round(alt_cost, 6),
                    "savings": round(savings, 6),
                    "savings_pct": round(savings / float(row.total_cost) * 100, 1)
                    if abs(float(row.total_cost)) > 1e-9
                    else 0,
                }
            )

        alternatives.sort(key=lambda x: x["savings"], reverse=True)
        entry["alternatives"] = alternatives[:5]
        results.append(entry)

    return results


def _run_timeline_query(intent: QueryIntent, db: Session) -> list[dict]:
    """Hourly/daily cost trend."""
    from app.database import engine

    if engine.dialect.name == "sqlite":
        ts_expr = func.strftime("%Y-%m-%d %H:00", CostEvent.timestamp)
    else:
        ts_expr = func.date_trunc("hour", CostEvent.timestamp)

    query = db.query(
        ts_expr.label("bucket"),
        func.sum(CostEvent.cost).label("cost"),
        func.count(CostEvent.id).label("requests"),
    )
    if intent.time_range:
        query = query.filter(
            CostEvent.timestamp >= intent.time_range[0],
            CostEvent.timestamp <= intent.time_range[1],
        )
    if intent.agents:
        query = query.filter(CostEvent.agent_name.in_(intent.agents))

    rows = (
        query.group_by("bucket").order_by("bucket").limit(168).all()
    )  # max 7 days hourly

    return [
        {
            "type": "timeline",
            "bucket": str(r.bucket),
            "cost": round(float(r.cost), 6),
            "requests": int(r.requests),
        }
        for r in rows
    ]


def _run_failures_query(intent: QueryIntent, db: Session) -> list[dict]:
    """Recent failed requests."""
    query = db.query(CostEvent).filter(CostEvent.status != "success")

    if intent.time_range:
        query = query.filter(
            CostEvent.timestamp >= intent.time_range[0],
            CostEvent.timestamp <= intent.time_range[1],
        )
    if intent.agents:
        query = query.filter(CostEvent.agent_name.in_(intent.agents))

    rows = query.order_by(CostEvent.timestamp.desc()).limit(20).all()

    return [
        {
            "type": "failure",
            "event_id": r.id,
            "agent_name": r.agent_name,
            "model_name": r.model_name,
            "provider_name": r.provider_name,
            "status": r.status,
            "cost": round(float(r.cost), 6),
            "duration_ms": r.duration_ms,
            "timestamp": r.timestamp.isoformat(),
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Retrieval context
# ---------------------------------------------------------------------------


@dataclass
class Citation:
    source_type: str  # "event" | "rollup" | "sql_query"
    source_id: str | None = None
    snippet: str = ""


@dataclass
class RetrievalContext:
    vector_results: list[dict] = field(default_factory=list)
    sql_results: list[dict] = field(default_factory=list)
    citations: list[Citation] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Main retrieval function
# ---------------------------------------------------------------------------


def retrieve(query: str, db: Session) -> RetrievalContext:
    """Hybrid retrieval: vector search + intent-based SQL queries."""
    ctx = RetrievalContext()

    intent = detect_query_intent(query, db)

    # 1. Vector search across both collections
    query_embedding = embed_text(query)

    # Build ChromaDB where filter
    where = None
    where_clauses = []
    if intent.agents and len(intent.agents) == 1:
        where_clauses.append({"agent_name": intent.agents[0]})
    if intent.providers and len(intent.providers) == 1:
        where_clauses.append({"provider_name": intent.providers[0]})
    if intent.needs_failures:
        where_clauses.append({"status": {"$ne": "success"}})

    if len(where_clauses) == 1:
        where = where_clauses[0]
    elif len(where_clauses) > 1:
        where = {"$and": where_clauses}

    event_results = search_events(
        query_embedding, n_results=settings.RAG_TOP_K, where=where
    )
    rollup_results = search_rollups(query_embedding, n_results=settings.RAG_TOP_K // 2)

    # Filter by similarity threshold (cosine distance: lower = more similar)
    threshold = settings.RAG_SIMILARITY_THRESHOLD
    for r in event_results:
        if r["distance"] is not None and r["distance"] <= (1 - threshold):
            ctx.vector_results.append(r)
            ctx.citations.append(
                Citation(
                    source_type="event",
                    source_id=r["id"],
                    snippet=r.get("document", "")[:200],
                )
            )

    for r in rollup_results:
        if r["distance"] is not None and r["distance"] <= (1 - threshold):
            ctx.vector_results.append(r)
            ctx.citations.append(
                Citation(
                    source_type="rollup",
                    source_id=r["id"],
                    snippet=r.get("document", "")[:200],
                )
            )

    # 2. Intent-based SQL queries for precise data
    if intent.needs_aggregation:
        agg = _run_aggregation_query(intent, db)
        ctx.sql_results.extend(agg)
        if agg:
            ctx.citations.append(
                Citation(
                    source_type="sql_query",
                    snippet=f"Aggregation query: {len(agg)} groups",
                )
            )

    if intent.needs_comparison:
        cmp = _run_comparison_query(intent, db)
        ctx.sql_results.extend(cmp)
        if cmp:
            ctx.citations.append(
                Citation(
                    source_type="sql_query",
                    snippet=f"Cost comparison: {len(cmp)} models",
                )
            )

    if intent.needs_timeline:
        tl = _run_timeline_query(intent, db)
        ctx.sql_results.extend(tl)
        if tl:
            ctx.citations.append(
                Citation(
                    source_type="sql_query",
                    snippet=f"Timeline: {len(tl)} data points",
                )
            )

    if intent.needs_failures:
        fails = _run_failures_query(intent, db)
        ctx.sql_results.extend(fails)
        if fails:
            ctx.citations.append(
                Citation(
                    source_type="sql_query",
                    snippet=f"Failures: {len(fails)} events",
                )
            )

    # Fallback: if no SQL results fired but the query looks data-related,
    # pull a general aggregation so Scrooge has real data to work with.
    any_data_intent = (
        intent.needs_aggregation
        or intent.needs_comparison
        or intent.needs_timeline
        or intent.needs_failures
        or intent.agents
        or intent.models
        or intent.providers
        or intent.time_range
    )
    if not ctx.sql_results and any_data_intent:
        fallback_intent = QueryIntent(
            needs_aggregation=True, time_range=intent.time_range
        )
        agg = _run_aggregation_query(fallback_intent, db)
        ctx.sql_results.extend(agg)
        if agg:
            ctx.citations.append(
                Citation(
                    source_type="sql_query",
                    snippet=f"General aggregation: {len(agg)} groups",
                )
            )

    return ctx


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------


def build_messages(
    query: str,
    context: RetrievalContext,
    history: list[dict] | None = None,
) -> list[dict]:
    """Build the message list for the LLM."""
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    # Build context block
    context_parts = []

    if context.vector_results:
        context_parts.append("## Relevant records from vector search")
        for i, r in enumerate(context.vector_results[:15]):
            doc = r.get("document", "")
            context_parts.append(f"[{i + 1}] {doc}")

    if context.sql_results:
        context_parts.append("\n## Structured query results")
        import json

        for r in context.sql_results[:20]:
            context_parts.append(json.dumps(r, default=str))

    if context_parts:
        context_text = "\n".join(context_parts)
        messages.append(
            {
                "role": "system",
                "content": f"Here is the relevant data to answer the user's question:\n\n{context_text}",
            }
        )

    # Append conversation history (last 10 turns)
    if history:
        for msg in history[-10:]:
            if msg.get("role") in ("user", "assistant"):
                messages.append({"role": msg["role"], "content": msg["content"]})

    # Append current query
    messages.append({"role": "user", "content": query})

    return messages
