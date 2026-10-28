# CMA — Cost Monitoring Agent

## Stack
- **Backend**: Python 3.10+ / FastAPI / SQLAlchemy 2.x / SQLite (dev) / PostgreSQL (prod)
- **Frontend**: React 18 / TypeScript / Vite / Tailwind CSS / Recharts
- **RAG**: ChromaDB (embedded) + sentence-transformers (all-MiniLM-L6-v2) + configurable LLM (OpenAI or Ollama)
- **SDK**: Python client (`sdks/python/cost_monitor/`) with auto-instrumentation for OpenAI, Anthropic, LangChain
- **Auth**: Bearer token in `Authorization` header, constant-time comparison (`hmac.compare_digest`). Destructive/sensitive admin endpoints require a separate `X-Admin-Password` (admin scope ≠ ingest key). API key stored plaintext (recoverable for the reconnect flow); admin password is bcrypt-hashed.

## Running locally
```powershell
# PowerShell (recommended)
.\cma-dev.ps1          # reads CMA_OPENAI_API_KEY from .env; avoid passing keys on the command line

# Without OpenAI key — uses Ollama (must be running locally)
.\cma-dev.ps1
```
- Backend: http://localhost:8000 (uvicorn --reload)
- Frontend: http://localhost:5173 (vite dev)
- API docs: http://localhost:8000/docs
- Health: http://localhost:8000/health
- Schema is managed by **Alembic** — run `cd backend; alembic upgrade head` before first start (the Docker entrypoint does this automatically)

## Running tests
```powershell
cd backend; pip install -r requirements-dev.txt; pytest
```
- 153 backend tests (`backend/tests`) + 14 SDK tests (`sdks/python/tests`, run separately: `cd sdks/python; pip install -e ".[dev,all]"; pytest tests`); isolated SQLite (schema built from models in `conftest.py`)
- `test_events.py` — ingestion, auto-discovery (incl. race), UTC timestamp normalisation
- `test_pricing.py` — pricing resolution, cost_source, reprice, reset-full reseed
- `test_scrooge_grounding.py` — Scrooge totals, windows, entity matching, untrusted data block, degradation
- `test_retention.py` — no spend lost on prune (incl. mid-prune inserts)
- `test_migrations.py` — migration data safety on seeded pre-upgrade data
- `test_auth.py` — setup + login
- `test_budgets.py` — enforcement + threshold alerts
- `test_dashboard.py` — metrics + rollups
- `test_setup.py` — initial setup flow
- `test_regressions.py` — envelope contract, embedder IDs, batch enforcement, admin gating, SSRF, rate limiting
- No frontend tests yet (no vitest/jest configured)
- Migrations: `cd backend; alembic upgrade head; alembic downgrade base; alembic upgrade head; alembic check` (CI runs this on Postgres)
- Always run before committing

## Environment
- All env vars prefixed with `CMA_` (Pydantic-settings in `backend/app/config.py`)
- `.env` file at project root (git-ignored)
- Key vars: `CMA_LLM_PROVIDER` (openai|ollama), `CMA_LLM_MODEL`, `CMA_OPENAI_API_KEY`
- `CMA_CHROMA_PERSIST_DIR` defaults to `./data/chroma`, `CMA_DATABASE_URL` defaults to `sqlite:///./data/cma.db`
- NEVER modify `.env` directly — ask the user

## Plan and findings
- `docs/PLAN.md` — baseline, stages with acceptance criteria, open owner decisions (D1–D4), progress log
- `docs/FINDINGS.md` — finding register (priority, evidence, action, stage, verification, status). Add new findings there; update status when fixing.

## Architecture decisions
- **ChromaDB is a cache, not source of truth** — SQL DB is authoritative, vector store rebuilt via backfill
- **Alembic owns the schema** — migrations are authoritative (`alembic upgrade head`); the app no longer runs `create_all`
- **Intent detection via regex, not LLM** — ~1ms vs ~500ms, LLM only for final answer generation
- **httpx for LLM calls, no SDK** — already a dependency, avoids version lock-in
- **Background embedding** — event ingestion stays fast, embedding happens async (BackgroundTasks)
- **Client-side chat history** — no session table, backend is stateless, history passed in request body
- **Soft-delete pattern** — providers, agents, channels use `is_active=False`, never hard-deleted from UI
- **Lazy singletons** — ChromaDB client, embedding model, LLM provider cached per process lifetime
- **Upsert-safe rollups** — daily aggregation is idempotent, safe to re-run
- **Retention safety** — per day, the rollup is written from exactly the event ids then deleted, in one transaction (rewrite for a never-pruned day, add for an archived one) and the day is marked `archived`; `compute_daily_rollups` never recomputes an archived day (its raw events are gone)
- **Pricing resolution** — provider by case-insensitive name or `provider_type`, model by exact name > alias list > ci name > date-snapshot-stripped; snapshot-only match = `cost_source='estimated'`; no match = `cost_source='unpriced'` at $0 (repriceable via `/admin/reprice`, batched)
- **UTC at the boundary** — storage is naive UTC; offset inputs are converted, every API datetime is emitted with `Z`
- **Scrooge data is untrusted** — retrieved data goes in a user-role `<data>` block; only SQL `total` rows (per explicit window, half-open) are presented as totals
- **Prometheus over OTel** — single process, no cross-service propagation to buy; RAG stage histograms are span-shaped (1:1 names) so an OTel migration stays mechanical. Metrics carry NO agent/model labels (unbounded cardinality); CMA's own rollups watch the spend, Prometheus watches the service
- **JSON file logging, human console** — `data/cma.log` is JSON lines (machine surface: /admin/logs, shippers); request IDs via contextvar middleware, no structlog (zero call-site changes)

## Database schema (9 tables)
| Table | Purpose | Key detail |
|-------|---------|-----------|
| `SetupConfig` | Single-row deployment config | api_key, admin_password_hash, timezone, currency |
| `Provider` | LLM provider definitions | name, provider_type, is_active (soft-delete) |
| `ModelPricing` | Per-model pricing | input/output price per million tokens, unique on provider+model |
| `Agent` | Auto-discovered from events | name, environment, swarm, workflow, is_active |
| `CostEvent` | Individual LLM calls | tokens_in/out, cost, cost_source (pricing/client/unpriced), trace_id, status, metadata_json |
| `Budget` | Cost limits | scope (global/agent/swarm/workflow), period (daily/weekly/monthly) |
| `Alert` | Triggered notifications | budget_id, severity, is_resolved |
| `AlertChannel` | Notification destinations | webhook, slack, or in_app |
| `DailyCostRollup` | Daily aggregations | unique on (date, agent, model, provider); `archived` once the day's events are pruned |

Indexes on: `CostEvent.timestamp`, `CostEvent.agent_name`

## Frontend structure
**Router** (in `App.tsx`):
- `/setup` — SetupWizard (no auth)
- `/` — Dashboard (protected by SetupGuard)
- `/chat` — Scrooge RAG interface
- `/agents`, `/agents/:id`, `/providers`, `/budgets`, `/alerts`, `/events`, `/integrations`, `/settings`

**Key components**: Layout (shell + Sidebar), ChatDrawer (slide-out from Dashboard), DataTable (paginated), ConfirmDialog (useId for a11y), StatsCard, Modal, Badge, EmptyState

**Hooks**: `useChat` (SSE streaming + citations + abort), `useApi` (generic CRUD with auth headers)

**State**: localStorage for API key + theme, no Redux/Zustand — component-level state throughout

## Key files
| File | What it does |
|------|-------------|
| `backend/app/services/rag_engine.py` | Hybrid RAG: intent detection + vector search + SQL queries + Scrooge prompt |
| `backend/app/services/vector_store.py` | ChromaDB client + sentence-transformers embedding + clear_vector_store() |
| `backend/app/services/llm_provider.py` | OpenAI/Ollama abstraction (LLMProvider ABC) with streaming |
| `backend/app/services/cost_engine.py` | Pricing resolution, cost = (tokens / 1M) * price, unpriced predicate, `reprice_events` |
| `backend/app/services/budget_checker.py` | Budget enforcement + threshold alerts (50/75/90/100%) with dedup |
| `backend/app/services/rollup.py` | Daily cost aggregation + ChromaDB embedding of rollups |
| `backend/app/services/retention.py` | Prune events older than retention_days (only if rolled up) |
| `backend/app/api/events.py` | Event ingestion — auto-creates agents, calculates cost, triggers budget checks |
| `backend/app/api/chat.py` | RAG chat endpoints (non-streaming + SSE) |
| `backend/app/api/admin.py` | Reset endpoints — clears both SQL AND ChromaDB |
| `backend/app/api/deps.py` | Auth middleware — API key validation, DB session injection |
| `backend/app/main.py` | Lifespan: seed providers + gated daily maintenance loop (schema via Alembic, not `create_all`) |
| `frontend/src/pages/Chat.tsx` | Full-page Scrooge chat interface |
| `frontend/src/hooks/useChat.ts` | SSE streaming chat hook with citations |
| `frontend/src/api/client.ts` | REST + SSE client, auto-clears key on 503 (setup reset) |
| `sdks/python/cost_monitor/tracker.py` | Python SDK core client for event ingestion |

## API structure
- Base: `/api/v1`
- Auth: `Authorization: Bearer <api_key>` on all routes except `/setup`, `/auth/login`
- Event ingestion: `POST /api/v1/events` (single, bare array, or `{"events": [...]}` envelope; batch capped by `CMA_MAX_EVENTS_PER_REQUEST`)
- Chat: `POST /api/v1/chat/stream` (SSE), `POST /api/v1/chat` (non-streaming)
- Semantic search: `POST /api/v1/search/semantic`
- Admin: `POST /api/v1/admin/reprice` (recompute unpriced events), `POST /api/v1/admin/reset` (monitoring only), `POST /api/v1/admin/reset-full` (everything; re-seeds default pricing) — destructive/sensitive admin routes (reprice, reset, reset-full, export, logs) require an `X-Admin-Password` header
- Observability: `GET /metrics` (Prometheus; API key or `CMA_METRICS_TOKEN`), `GET /health` (liveness), `GET /health/ready` (readiness: Postgres hard-fails 503, vector store degrades) — health endpoints unauthenticated, all three excluded from HTTP metrics
- Full Swagger at `/docs`

## Background tasks
- **On event ingestion**: embed to ChromaDB + check budgets (via FastAPI BackgroundTasks)
- **Daily maintenance** (every 24h after 10s startup delay; gated by `CMA_ENABLE_MAINTENANCE`, default on — disable on all but one worker/replica):
  - `compute_daily_rollups()` — aggregate yesterday's events
  - `backfill_event_embeddings()` — catch any un-embedded events
  - `prune_old_events()` — retention cleanup
- Maintenance runs in-process, no external scheduler needed

## Deployment (Docker)
```powershell
docker compose up -d
# with the metrics rig (Prometheus :9090 + Grafana :3000, provisioned dashboard):
docker compose --profile observability up -d
```
- 3 services: postgres:16-alpine, backend (python:3.11-slim), frontend (nginx:alpine); `--profile observability` adds prometheus + grafana (set `CMA_METRICS_TOKEN` in `.env` AND write the same value to `deploy/prometheus/metrics-token` — git-ignored, see `.example`)
- Startup order enforced via healthchecks (postgres -> backend -> frontend)
- Backend Dockerfile installs CPU-only PyTorch for sentence-transformers
- Frontend uses multi-stage build (node:20 -> nginx)
- Volumes: `pg-data` (postgres), `chroma-data` (vector store)

## Gotchas
- If events show `cost: 0` — check `cost_source`: `unpriced` means no price row matched (add the model or an alias under Providers, then Settings → Reprice); `client` means the caller sent 0
- After admin reset, ChromaDB is cleared automatically (`clear_vector_store()` in both reset endpoints)
- `cma-dev.ps1` starts uvicorn with `--reload` but file watching is unreliable on Windows — restart manually if code changes aren't picked up
- The RAG intent detector uses regex patterns in `_AGG_PATTERNS`, `_CMP_PATTERNS`, etc. — if a query type doesn't trigger SQL, check these patterns first
- Fallback aggregation only fires when at least one data intent is detected — pure conversational queries get vector-only context
- `gpt-4o-mini` doesn't lean hard into the Scrooge personality — larger models follow the system prompt more faithfully
- Entity matching in intent detection is whole-token; overlapping spans resolve longest-first ("gpt-4" is not matched inside "gpt-4.1", but "gpt-4 and gpt-4o" keeps both)
- Budget periods, dashboard days and Scrooge windows are UTC; `SetupConfig.timezone`/`currency` are stored but unused (decision D2/ACC-16)
- `stop` budget enforcement rejects events at ingestion, after the LLM call — it hides spend rather than preventing it; UI `block`/`throttle` are not enforced (decision D1)
- `int(row.total_tokens_in)` can throw TypeError if NULL — always use `int(x or 0)` pattern
- API key stored as plaintext in SetupConfig despite bcrypt being imported — password uses hash, key does not
- On Windows, npm/npx are .cmd shims — `Start-Process` needs `cmd.exe /c npm`, not `npm` directly
- Vite proxy (`/api` -> localhost:8000) only works in dev; prod nginx handles routing
- `frontend/package-lock.json` is **committed** (keep it so) — CI/local/Docker install identical deps. A floating lockfile previously broke `frontend-build` twice (stricter TS; `simple-icons` dropping the trademarked OpenAI logo, now inlined). Tests create the `data/` dir + use an isolated DB since Alembic, not `create_all`, owns the schema.

## Scrooge (RAG assistant)
- Personality defined in `SYSTEM_PROMPT` in `rag_engine.py`
- Blend: money-obsessed (Scrooge), pattern-curious (data astronomer), warm but no padding
- Rules: lead with numbers, cite sources in brackets, USD formatting, absolute + percentage comparisons, max 300 words
- Named "Scrooge" across all UI: Chat page header, Sidebar nav, ChatDrawer, starter cards
- System prompt instructs: no disclaimers, no padding, genuine curiosity about data anomalies

## Known limitations
- No frontend tests (vitest/jest not configured)
- Event ingestion has a batch-size cap but no per-request-rate limit (login + chat are rate-limited in-process, per-IP)
- No WebSocket — Dashboard uses polling, Chat uses SSE
- Trace ID stored but no trace tree visualization
- Alert channels limited to webhook, Slack, in-app (no email/Discord)
- No cost forecasting (rollup data ready for it, model not built)
- Semantic search thresholds (`RAG_TOP_K`, `RAG_SIMILARITY_THRESHOLD`) not exposed in UI
