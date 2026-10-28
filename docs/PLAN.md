# CMA completion and stabilization plan

Scope: finish and harden CMA within its existing purpose — self-hosted cost
observability for LLM / multi-agent systems (attribution, budgets, alerts, Scrooge).
No new product areas. Findings and their verification live in
[FINDINGS.md](FINDINGS.md); this file holds the baseline, stages, decisions and
progress.

## Baseline (2026-10-28)

Starting point: branch `master`, commit `3c0e648`. CI on GitHub was green for that
commit (all five jobs). Local reproduction on Linux, Python 3.11, Node 22,
PostgreSQL 16:

| Check | Command | Result |
|---|---|---|
| Lint | `ruff check backend/ && ruff format --check backend/` (ruff 0.16.9) | pass |
| Backend tests | `cd backend && pytest -q` | 81 passed |
| SDK tests | `cd sdks/python && pip install -e ".[dev,all]" && pytest tests` (openai 3.40, anthropic 2.3, langchain-core 1.7) | 14 passed |
| Migrations, Postgres | `alembic upgrade head && alembic downgrade base && alembic upgrade head` | pass |
| Model/migration drift | `alembic check` | no drift |
| Frontend | `cd frontend && npm ci --ignore-scripts && npm run build` | pass (700 kB chunk warning) |
| Frontend image | `docker build ./frontend` | pass |
| Backend image | `docker build ./backend` | **blocked locally**: the sandbox proxy's CA is not trusted inside the build container (PyTorch wheel index TLS). Covered by CI `docker-build`. |
| Real LLM answers (Scrooge) | — | **not verified**: no model access in the audit environment; all Scrooge checks stub the LLM and embeddings. |
| Embedding model quality | — | **not verified**: `all-MiniLM-L6-v2` not downloadable here; vector paths tested with stubs. |

Documented test count was 89; actual was 95 (81 + 14).

**Documented vs implemented vs verified, headline gaps:** the README promised
auto-instrumented cost tracking, hard-stop budgets and evidence-backed answers.
Implemented: tracking existed, but SDK events priced at $0 (ACC-01); "stop" dropped
already-incurred spend and UI budget actions never enforced (BUD-01/02); Scrooge
totals could be truncated or for the wrong window (RAG-01..04). Verified: each by a
reproduction listed in FINDINGS.md.

## Stages and acceptance criteria

**S0 Baseline** — Done. Reproducible commands above; results recorded.

**S1 Accounting integrity** — Done in PR `fix/accounting-integrity`.
Accept when: SDK-spelled provider/model strings price correctly; offset timestamps
are stored as UTC on SQLite and Postgres; concurrent ingestion never drops events;
unpriced events are counted, visible, and repriceable; retention never removes spend
from rollups; API datetimes are explicit UTC; migration 002 upgrades/downgrades on
SQLite and Postgres with no drift. *Met — see ACC-01..07, 13, 15; BUD-03.*

**S2 Grounded Scrooge** — Partly done in the same PR.
Accept when: every total in the prompt is an exact SQL SUM for a stated window; any
period the question names is either computed or explicitly reported as not
understood; entity filters match whole names; ingested text never has system
authority; empty data and vector outages are stated, not hidden; citations returned
are ones the answer could use (RAG-09); vector records are time-scoped (RAG-10); a
golden-question context test runs in CI (RAG-14).
*Met: RAG-01..08. Open: RAG-09, RAG-10, RAG-14.*

**S3 Budget semantics** — Blocked on D1, D2.
Accept when: every budget action selectable in the UI has tested behaviour; incurred
spend is always recorded; the guarantee (soft limit, timing, concurrency) is
documented; period boundaries follow the chosen time zone and are labelled; one
alert per threshold per period under concurrency (BUD-06).

**S4 Instrumentation coverage and delivery** — ACC-08/09 blocked on D3; rest open.
Accept when: every listed OpenAI/Anthropic surface (sync, async, stream, parse, raw)
yields exactly one event with correct tokens; the SDK never changes what user code
receives (SDK-02); partial streams keep their tokens; no double counting with
LangChain; fork-safe; one bad event cannot drop others; exit flush works; duplicate
sends are idempotent (ACC-10).

**S5 Security hardening** — SEC-01 blocked on D4; rest open.
Accept when: credentials and inputs cannot produce 500s; SSRF guard pins resolved
addresses and blocks non-global ranges; metadata and chat history are size-capped;
no webhook secrets in logs/responses; proxy deployment rate limiting is per client.

**S6 Operations, docs, UX** — Open.
Accept when: backup/restore runbook exercised; dashboards and Scrooge use rollups for
pruned days (ACC-14); seed pricing verified against official pages with a date;
status endpoints truthful; Events page shows cost source.

Order of work after S1/S2: D1–D4 answers → S3 → S4 (SDK-01, SDK-02 first) → S5 → S6.
Each stage ships as its own reviewable PR.

## Decisions

Recorded design decisions (made during S1/S2):

- **Pricing resolution** is tolerant (case-insensitive provider name or type, alias
  list, date-snapshot fallback) because the SDK and the UI never agreed on spelling.
  Exact matches always win; no fuzzy matching beyond a trailing date suffix.
- **Unpriced ≠ free.** `cost_source` records how a cost was determined; unpriced
  events are repriceable. Client-supplied costs are never recomputed.
- **Storage stays naive UTC**; the API boundary converts offsets in and emits `Z` out.
- **Scrooge data is untrusted input**: sent in a user-role `<data>` block; the only
  numbers presented as totals are SQL SUM rows for an explicit window.
- **Budget validation accepts both historical action vocabularies** until D1 decides
  their meaning, so no existing client breaks.

Open decisions (owner input needed — they change semantics, compatibility or schema):

- **D1 Budget enforcement.** Ingestion-time rejection hides real spend (BUD-02), and
  the UI's actions are not enforced (BUD-01). Options: (a) always record; enforce via
  an opt-in SDK pre-call check that raises before the LLM call, with over-budget
  events flagged; (b) keep ingestion rejection and map UI values to it;
  (c) alert-only for now, remove block/throttle from the UI.
- **D2 Time zone of periods.** (a) Use the deployment time zone from setup for budget
  periods, dashboard days, rollup days and Scrooge windows (rollups recomputed once;
  DST handled by `zoneinfo`); (b) keep UTC and label it everywhere, removing the
  setup choice.
- **D3 Cached-token accounting.** (a) New event fields for cache read/write (and
  OpenAI cached input) plus per-model cache prices — exact, needs migration and UI;
  (b) SDK folds cache tokens into billed-equivalent input tokens with fixed
  multipliers (no schema change, loses detail, multipliers can drift).
- **D4 Credentials.** (a) Separate revocable ingest keys; management writes (pricing,
  budgets, channels) require admin — breaks scripts that write with the ingest key;
  (b) keep one key, add rotation; (c) defer.

## Progress log

- 2026-10-28 — Baseline recorded. Audit of accounting, budgets, SDK, Scrooge,
  security, ops. PR `fix/accounting-integrity`: S1 complete, S2 RAG-01..08.
  Backend tests 81 → 134; SDK 14 (unchanged). Decisions D1–D4 raised.
  CI green on PR #22 (all five jobs, incl. the backend image build blocked
  locally). Codex review: two valid findings in this PR's own changes
  (archived-rollup overwrite in retention, overlapping entity names) fixed
  with migration 003 + tests.
