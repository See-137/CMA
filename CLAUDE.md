# CMA — Cost Monitoring Agent

## Stack
- **Backend**: Python 3.10+ / FastAPI / SQLAlchemy 2.x / SQLite (dev) / PostgreSQL (prod)
- **Frontend**: React 18 / TypeScript / Vite / Tailwind CSS / Recharts
- **RAG**: ChromaDB (embedded) + sentence-transformers (all-MiniLM-L6-v2) + configurable LLM (OpenAI or Ollama)
- **Auth**: Bearer token in `Authorization` header, bcrypt-hashed API key in SetupConfig

## Running locally
```bash
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

## Running tests
```bash
cd backend && pytest
```
- 22 tests, in-memory SQLite, fixtures in `conftest.py`
- Always run before committing

## Environment
- All env vars prefixed with `CMA_` (Pydantic-settings)
- `.env` file at project root (git-ignored)
- Key vars: `CMA_LLM_PROVIDER` (openai|ollama), `CMA_LLM_MODEL`, `CMA_OPENAI_API_KEY`
- NEVER modify `.env` directly — ask the user

## Architecture decisions
- **ChromaDB is a cache, not source of truth** — SQL DB is authoritative, vector store can be fully rebuilt via backfill
- **Intent detection via regex, not LLM** — ~1ms vs ~500ms, LLM only used for final answer generation
- **httpx for LLM calls, no SDK** — already a dependency, avoids version lock-in
- **Background embedding** — event ingestion stays fast, embedding happens async (BackgroundTasks)
- **Client-side chat history** — no session table, backend is stateless, history passed in request body
- **Soft-delete pattern** — providers, agents, channels use `is_active=False`, never hard-deleted from UI

## Key files
| File | What it does |
|------|-------------|
| `backend/app/services/rag_engine.py` | Hybrid RAG: intent detection + vector search + SQL queries + Scrooge prompt |
| `backend/app/services/vector_store.py` | ChromaDB client + sentence-transformers embedding |
| `backend/app/services/llm_provider.py` | OpenAI/Ollama abstraction with streaming support |
| `backend/app/services/cost_engine.py` | Cost calculation from token counts + model pricing |
| `backend/app/api/events.py` | Event ingestion — auto-creates agents, calculates cost, triggers budget checks |
| `backend/app/api/chat.py` | RAG chat endpoints (non-streaming + SSE) |
| `backend/app/api/admin.py` | Reset endpoints — clears both SQL AND ChromaDB |
| `frontend/src/pages/Chat.tsx` | Full-page Scrooge chat interface |
| `frontend/src/hooks/useChat.ts` | SSE streaming chat hook |
| `frontend/src/components/ChatDrawer.tsx` | Slide-out chat panel (from Dashboard) |

## API structure
- Base: `/api/v1`
- Auth: `Authorization: Bearer <api_key>` (all routes except `/setup`, `/auth/login`)
- Event ingestion: `POST /api/v1/events` (single or batch)
- Chat: `POST /api/v1/chat/stream` (SSE), `POST /api/v1/chat` (non-streaming)
- Semantic search: `POST /api/v1/search/semantic`
- Full Swagger at `/docs`

## Gotchas
- If events show `cost: 0` — check that model pricing exists in `model_pricing` table for the exact model name + provider combo
- After admin reset, ChromaDB must also be cleared (`clear_vector_store()` is called automatically)
- `cma-dev.bat` starts uvicorn with `--reload` but file watching is unreliable on Windows — restart manually if code changes aren't picked up
- The RAG intent detector uses regex patterns in `_AGG_PATTERNS`, `_CMP_PATTERNS`, etc. — if a query type doesn't trigger SQL, check these patterns first
- Fallback aggregation only fires when at least one data intent is detected (time range, entity mention, or keyword match) — pure conversational queries get vector-only context
- `gpt-4o-mini` doesn't lean hard into the Scrooge personality — larger models follow the system prompt more faithfully

## Scrooge (RAG assistant)
- Personality defined in `SYSTEM_PROMPT` in `rag_engine.py`
- Rules: lead with numbers, cite sources in brackets, USD formatting, absolute + percentage comparisons
- Named "Scrooge" across all UI: Chat page header, Sidebar nav, ChatDrawer, starter cards
- System prompt instructs: no disclaimers, no padding, genuine curiosity about data anomalies
