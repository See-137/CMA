"""Prometheus metrics for CMA self-observability.

Cardinality rule (NON-NEGOTIABLE): no metric here may carry agent_name,
model, or any other user-supplied label. Agent names arrive from client
SDKs in unbounded variety; label them and the TSDB bloats until it OOMs.
Prometheus watches the SERVICE (throughput, latency, backlog, job
freshness); CMA's own SQL rollups watch the SPEND (per-agent/per-model,
bounded by the dashboard's own queries).

Process-scope caveat: the registry is per-process. Under multi-worker
uvicorn each worker exposes its own counters (PROMETHEUS_MULTIPROC_DIR
would be needed to merge them — documented, not built; the current deploy
is single-process), and maintenance metrics exist only on the
CMA_ENABLE_MAINTENANCE=true worker.
"""

import logging
import time

from prometheus_client import REGISTRY, Counter, Gauge, Histogram
from prometheus_client.core import GaugeMetricFamily

logger = logging.getLogger(__name__)

EVENTS_INGESTED = Counter(
    "cma_events_ingested_total",
    "Cost events ingested, by outcome",
    ["status"],  # processed | rejected | errored
)

# Buckets sit BELOW the backend cap (CMA_MAX_EVENTS_PER_REQUEST, default
# 1000): a bucket equal to the cap is always identical to +Inf and carries
# no information.
INGEST_BATCH_SIZE = Histogram(
    "cma_ingest_batch_size",
    "Events per accepted ingestion request (413-rejected batches excluded)",
    buckets=(1, 10, 50, 100, 250, 500, 750),
)

# Named for what it actually times — loading enforcement state, a small SQL
# aggregation — with sub-millisecond buckets to match; the default 5ms-10s
# buckets would put every observation in the first bucket.
BUDGET_STATE_LOAD_SECONDS = Histogram(
    "cma_budget_state_load_seconds",
    "Time loading budget-enforcement state per ingestion request",
    buckets=(0.0005, 0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1, 0.5),
)

MAINTENANCE_LAST_SUCCESS = Gauge(
    "cma_maintenance_last_success_timestamp_seconds",
    "Unix timestamp of the last successful run, per maintenance job",
    ["job"],
)

MAINTENANCE_FAILURES = Counter(
    "cma_maintenance_failures_total",
    "Failed maintenance job runs, per job",
    ["job"],
)

SCRAPE_ERRORS = Counter(
    "cma_metrics_scrape_errors_total",
    "Errors computing scrape-time gauges (DB/vector store unreachable)",
)

# Stage names are 1:1 with the OTel span names a future tracing migration
# would use — the histogram IS the span breakdown, minus the waterfall UI.
# Buckets span the union of stage costs: low-millisecond intent detection
# (regex + three entity-name SELECTs) up to multi-second LLM generation.
#
# Reading notes (measurement honesty):
# - llm_total is observed ONLY for completed generations — time-to-failure
#   and truncated-disconnect samples would collapse the percentiles during
#   incidents. Failure rates live in cma_chat_requests_total.
# - Stream-mode llm_total spans provider start to stream end, which
#   includes SSE write backpressure from slow clients — it is end-to-end
#   stream duration, not pure provider time.
# - sql_queries is observed on every request, including ones where no data
#   intent fires (a near-zero no-op sample); the lowest bucket approximates
#   the conversational/no-op traffic fraction.
# - Stages do NOT sum to request wall time: the asyncio.to_thread queue
#   wait before retrieve() starts is unanchored by design.
RAG_STAGE_SECONDS = Histogram(
    "cma_rag_stage_seconds",
    "RAG pipeline stage latency",
    ["stage"],
    buckets=(0.001, 0.005, 0.025, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0),
)

RAG_STAGE_NAMES = (
    "intent",
    "embed_query",
    "vector_search",
    "sql_queries",
    "llm_first_token",
    "llm_total",
)

# Counted at the SSE serialization point: streaming LLM failures ship as
# {"type": "error"} data events inside an HTTP 200 — invisible to every
# status-code-based monitor, so HTTP metrics alone cannot see them.
CHAT_REQUESTS = Counter(
    "cma_chat_requests_total",
    "Chat requests by transport mode and outcome",
    ["mode", "outcome"],  # mode: sync|stream; outcome: ok|llm_error|cancelled
)

CHAT_MODES = ("sync", "stream")
CHAT_OUTCOMES = ("ok", "llm_error", "cancelled")

MAINTENANCE_JOB_NAMES = ("rollup", "retention", "embed_backfill")

# Successful backlog computations are cached briefly: the gauge needs a full
# COUNT over cost_events — the one table that grows without bound — and a
# 15s scrape interval must not turn that into a permanent O(n) query load.
# Failures are never cached; an outage must stay visible on every scrape.
_BACKLOG_TTL_SECONDS = 30.0
_backlog_cache: tuple[float, int] | None = None  # (monotonic_ts, value)


def _count_sql_events() -> int:
    from sqlalchemy import func, select

    from app.database import SessionLocal
    from app.models.models import CostEvent

    db = SessionLocal()
    try:
        return db.execute(select(func.count(CostEvent.id))).scalar() or 0
    finally:
        db.close()


def _count_vector_events() -> int:
    from app.services.vector_store import get_events_collection

    return get_events_collection().count()


class BacklogCollector:
    """Scrape-time gauge: events in SQL not yet in the vector store.

    Clamped at zero — best-effort vector deletes can orphan embeddings,
    making the raw difference negative. A computation failure increments
    SCRAPE_ERRORS and OMITS the gauge for that scrape (absent beats stale
    or fabricated-zero): losing one gauge during an outage beats 500ing
    the whole exposition during exactly the incident metrics exist to
    observe.
    """

    def describe(self):
        """Registration-time metadata WITHOUT computing values.

        Without this, CollectorRegistry(auto_describe=True) calls collect()
        inside register() — running a SQL COUNT and initializing ChromaDB
        as a side effect of merely importing the app.
        """
        return []

    def collect(self):
        global _backlog_cache
        family = GaugeMetricFamily(
            "cma_embedding_backlog_events",
            "Events awaiting embedding (SQL count minus vector count, clamped at zero)",
        )
        now = time.monotonic()
        if (
            _backlog_cache is not None
            and now - _backlog_cache[0] < _BACKLOG_TTL_SECONDS
        ):
            family.add_metric([], _backlog_cache[1])
            yield family
            return
        try:
            backlog = max(0, _count_sql_events() - _count_vector_events())
            _backlog_cache = (now, backlog)
            family.add_metric([], backlog)
        except Exception:
            SCRAPE_ERRORS.inc()
            logger.debug(
                "Backlog gauge computation failed at scrape time", exc_info=True
            )
        yield family


_initialized = False


def init_metrics() -> None:
    """Register scrape-time collectors and zero-init labeled counters,
    exactly once per process.

    Zero-init matters: a labeled series is born at its first increment,
    which rate()/increase() cannot see — the first rejection or first
    maintenance failure after deploy would be invisible to alerts without
    a pre-existing zero sample. (MAINTENANCE_LAST_SUCCESS is deliberately
    NOT pre-set — a fabricated timestamp would defeat its freshness
    purpose.)
    """
    global _initialized
    if _initialized:
        return
    _initialized = True
    REGISTRY.register(BacklogCollector())
    for status in ("processed", "rejected", "errored"):
        EVENTS_INGESTED.labels(status=status)
    for job in MAINTENANCE_JOB_NAMES:
        MAINTENANCE_FAILURES.labels(job=job)
    for stage in RAG_STAGE_NAMES:
        RAG_STAGE_SECONDS.labels(stage=stage)
    for mode in CHAT_MODES:
        for outcome in CHAT_OUTCOMES:
            CHAT_REQUESTS.labels(mode=mode, outcome=outcome)
