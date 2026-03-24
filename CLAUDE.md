# CMA — Cost Monitoring Agent

## Stack
- **Backend**: Python 3.10+ / FastAPI / SQLAlchemy 2.x / SQLite (dev) / PostgreSQL (prod)
- **Frontend**: React 18 / TypeScript / Vite / Tailwind CSS / Recharts
- **RAG**: ChromaDB (embedded) + sentence-transformers (all-MiniLM-L6-v2) + configurable LLM (OpenAI or Ollama)
- **SDK**: Python client (`sdks/python/cost_monitor/`) with auto-instrumentation for OpenAI, Anthropic, LangChain
- **Auth**: Bearer token in `Authorization` header, plaintext API key comparison (not hashed despite bcrypt import)

## Running locally
```powershell
# PowerShell (recommended)
.\cma-dev.ps1 -Key sk-proj-YOUR-KEY

# cmd.exe
cma-dev.bat sk-proj-YOUR-KEY

# Without OpenAI key — uses Ollama (must be running locally)
.\cma-dev.ps1
```
- Backend: http://localhost:8000 (uvicorn --reload)
- Frontend: http://localhost:5173 (vite dev)
- API docs: http://localhost:8000/docs
- Health: http://localhost:8000/health

## Running tests
```powershell
cd backend; pytest
```
- ~22 tests across 5 modules, in-memory SQLite, fixtures in `conftest.py`
- `test_events.py` — ingestion, auto-discovery, cost calc
- `test_auth.py` — setup + login
- `test_budgets.py` — enforcement + threshold alerts
- `test_dashboard.py` — metrics + rollups
- `test_setup.py` — initial setup flow
- No frontend tests yet (no vitest/jest configured)
- Always run before committing

## Environment
- All env vars prefixed with `CMA_` (Pydantic-settings in `backend/app/config.py`)
- `.env` file at project root (git-ignored)
- Key vars: `CMA_LLM_PROVIDER` (openai|ollama), `CMA_LLM_MODEL`, `CMA_OPENAI_API_KEY`
- `CMA_CHROMA_PERSIST_DIR` defaults to `./data/chroma`, `CMA_DATABASE_URL` defaults to `sqlite:///./data/cma.db`
- NEVER modify `.env` directly — ask the user

## Architecture decisions
- **ChromaDB is a cache, not source of truth** — SQL DB is authoritative, vector store rebuilt via backfill
- **Intent detection via regex, not LLM** — ~1ms vs ~500ms, LLM only for final answer generation
- **httpx for LLM calls, no SDK** — already a dependency, avoids version lock-in
- **Background embedding** — event ingestion stays fast, embedding happens async (BackgroundTasks)
- **Client-side chat history** — no session table, backend is stateless, history passed in request body
- **Soft-delete pattern** — providers, agents, channels use `is_active=False`, never hard-deleted from UI
- **Lazy singletons** — ChromaDB client, embedding model, LLM provider cached per process lifetime
- **Upsert-safe rollups** — daily aggregation is idempotent, safe to re-run
- **Retention safety** — only prunes events for dates with completed rollups (prevents data loss)

## Database schema (9 tables)
| Table | Purpose | Key detail |
|-------|---------|-----------|
| `SetupConfig` | Single-row deployment config | api_key, admin_password_hash, timezone, currency |
| `Provider` | LLM provider definitions | name, provider_type, is_active (soft-delete) |
| `ModelPricing` | Per-model pricing | input/output price per million tokens, unique on provider+model |
| `Agent` | Auto-discovered from events | name, environment, swarm, workflow, is_active |
| `CostEvent` | Individual LLM calls | tokens_in/out, cost, trace_id, status, metadata_json |
| `Budget` | Cost limits | scope (global/agent/swarm/workflow), period (daily/weekly/monthly) |
| `Alert` | Triggered notifications | budget_id, severity, is_resolved |
| `AlertChannel` | Notification destinations | webhook, slack, or in_app |
| `DailyCostRollup` | Daily aggregations | unique on (date, agent, model, provider) |

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
| `backend/app/services/cost_engine.py` | Cost = (tokens / 1M) * price, returns 0.0 if pricing missing |
| `backend/app/services/budget_checker.py` | Budget enforcement + threshold alerts (50/75/90/100%) with dedup |
| `backend/app/services/rollup.py` | Daily cost aggregation + ChromaDB embedding of rollups |
| `backend/app/services/retention.py` | Prune events older than retention_days (only if rolled up) |
| `backend/app/api/events.py` | Event ingestion — auto-creates agents, calculates cost, triggers budget checks |
| `backend/app/api/chat.py` | RAG chat endpoints (non-streaming + SSE) |
| `backend/app/api/admin.py` | Reset endpoints — clears both SQL AND ChromaDB |
| `backend/app/api/deps.py` | Auth middleware — API key validation, DB session injection |
| `backend/app/main.py` | Lifespan: create tables, seed providers, daily maintenance loop |
| `frontend/src/pages/Chat.tsx` | Full-page Scrooge chat interface |
| `frontend/src/hooks/useChat.ts` | SSE streaming chat hook with citations |
| `frontend/src/api/client.ts` | REST + SSE client, auto-clears key on 503 (setup reset) |
| `sdks/python/cost_monitor/tracker.py` | Python SDK core client for event ingestion |

## API structure
- Base: `/api/v1`
- Auth: `Authorization: Bearer <api_key>` on all routes except `/setup`, `/auth/login`
- Event ingestion: `POST /api/v1/events` (single or batch array)
- Chat: `POST /api/v1/chat/stream` (SSE), `POST /api/v1/chat` (non-streaming)
- Semantic search: `POST /api/v1/search/semantic`
- Admin: `POST /api/v1/admin/reset` (monitoring only), `POST /api/v1/admin/reset-full` (everything)
- Full Swagger at `/docs`

## Background tasks
- **On event ingestion**: embed to ChromaDB + check budgets (via FastAPI BackgroundTasks)
- **Daily maintenance** (runs every 24h after 10s startup delay in `main.py` lifespan):
  - `compute_daily_rollups()` — aggregate yesterday's events
  - `backfill_event_embeddings()` — catch any un-embedded events
  - `prune_old_events()` — retention cleanup
- Maintenance runs in-process, no external scheduler needed

## Deployment (Docker)
```powershell
docker compose up -d
```
- 3 services: postgres:16-alpine, backend (python:3.11-slim), frontend (nginx:alpine)
- Startup order enforced via healthchecks (postgres -> backend -> frontend)
- Backend Dockerfile installs CPU-only PyTorch for sentence-transformers
- Frontend uses multi-stage build (node:18 -> nginx)
- Volumes: `pg-data` (postgres), `chroma-data` (vector store)

## Gotchas
- If events show `cost: 0` — check that model pricing exists in `model_pricing` table for the exact model name + provider combo
- After admin reset, ChromaDB is cleared automatically (`clear_vector_store()` in both reset endpoints)
- `cma-dev.bat` starts uvicorn with `--reload` but file watching is unreliable on Windows — restart manually if code changes aren't picked up
- The RAG intent detector uses regex patterns in `_AGG_PATTERNS`, `_CMP_PATTERNS`, etc. — if a query type doesn't trigger SQL, check these patterns first
- Fallback aggregation only fires when at least one data intent is detected — pure conversational queries get vector-only context
- `gpt-4o-mini` doesn't lean hard into the Scrooge personality — larger models follow the system prompt more faithfully
- Entity matching in intent detection is substring-based — short agent/model names (e.g., "gpt") will false-match longer ones (e.g., "gpt-4o")
- `int(row.total_tokens_in)` can throw TypeError if NULL — always use `int(x or 0)` pattern
- API key stored as plaintext in SetupConfig despite bcrypt being imported — password uses hash, key does not
- On Windows, npm/npx are .cmd shims — `Start-Process` needs `cmd.exe /c npm`, not `npm` directly
- Vite proxy (`/api` -> localhost:8000) only works in dev; prod nginx handles routing

## Scrooge (RAG assistant)
- Personality defined in `SYSTEM_PROMPT` in `rag_engine.py`
- Blend: money-obsessed (Scrooge), pattern-curious (data astronomer), warm but no padding
- Rules: lead with numbers, cite sources in brackets, USD formatting, absolute + percentage comparisons, max 300 words
- Named "Scrooge" across all UI: Chat page header, Sidebar nav, ChatDrawer, starter cards
- System prompt instructs: no disclaimers, no padding, genuine curiosity about data anomalies

## Known limitations
- No frontend tests (vitest/jest not configured)
- No rate limiting on event ingestion or chat endpoints
- No WebSocket — Dashboard uses polling, Chat uses SSE
- Trace ID stored but no trace tree visualization
- Alert channels limited to webhook, Slack, in-app (no email/Discord)
- No cost forecasting (rollup data ready for it, model not built)
- Semantic search thresholds (`RAG_TOP_K`, `RAG_SIMILARITY_THRESHOLD`) not exposed in UI
