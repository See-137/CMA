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

**S3 Budget semantics** — D1 done (#23); D2 time zone done (`feat/deployment-timezone`); BUD-06 done (`feat/alert-dedup`); BUD-05 docs open.
Accept when: every budget action selectable in the UI has tested behaviour; incurred
spend is always recorded; the guarantee (soft limit, timing, concurrency) is
documented; period boundaries follow the chosen time zone and are labelled; one
alert per threshold per period under concurrency (BUD-06).

**S4 Instrumentation coverage and delivery** — Open (ACC-08/09 per D3).
Accept when: every listed OpenAI/Anthropic surface (sync, async, stream, parse, raw)
yields exactly one event with correct tokens; the SDK never changes what user code
receives (SDK-02); partial streams keep their tokens; no double counting with
LangChain; fork-safe; one bad event cannot drop others; exit flush works; duplicate
sends are idempotent (ACC-10).

**S5 Security hardening** — Open (SEC-01 per D4).
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

Owner decisions (2026-10-28):

- **D1 Budget enforcement → record + pre-call check** (owner choice). Spend is
  always stored; events past an exhausted blocking budget are flagged
  `over_budget`; `GET /budgets/check` and SDK `enforce_budgets=True` raise
  `BudgetExceeded` before the provider is contacted. Soft limit: checks are
  cached per TTL and not serialised across callers. Every action except
  `alert` blocks; `throttle`/`rate_limit`/`model_downgrade` are legacy aliases.
  *Implemented in `feat/budget-enforcement`.*
- **D2 Time zone → deployment time zone** (owner delegated the choice). Why:
  budgets are operator-facing ("daily" should reset at the operator's
  midnight, not 02:00/03:00 local in UTC+2/+3), the setup wizard already asks
  for a zone, and storage stays UTC so only boundaries change. Default stays
  UTC, so unconfigured installs see no change. Cost: rollup keys and
  dashboard/Scrooge buckets move to local days (rollups recomputed once from
  raw events); archived days whose events are gone keep their UTC keys —
  documented, not migrated. Rejected alternative (UTC everywhere, labelled)
  is simpler but leaves the setup choice meaningless and "today" wrong for
  every non-UTC operator. *Implemented in `feat/deployment-timezone` for every
  user-facing boundary; rollup re-keying deferred to ACC-14 (ACC-18): rollups
  are not user-facing yet, so re-keying now would be a data migration with no
  visible benefit.*
- **D3 Cached tokens → exact fields + per-model cache prices** (owner choice).
- **D4 Credentials → split credentials with a compatibility window** (owner delegated the choice; decided by the implementation lead): split
  credentials *with a compatibility window* rather than a breaking cut-over.
  (1) Add hashed, named, revocable **ingest keys** (several; only
  `POST /events` and `GET /budgets/check`). (2) Management reads/writes
  (pricing, budgets, channels, events, chat) use an **admin session token**
  issued at login (short-lived, revocable) instead of returning the master
  key. (3) The existing master key keeps working for everything during a
  deprecation window, with a UI banner and a log warning on management use;
  after the window it becomes an ingest-only key. (4) Rotation for every key.
  This closes the "one leaked SDK key edits pricing" hole without breaking
  existing SDK deployments or scripts on upgrade day.

## Progress log

- 2026-10-28 — Baseline recorded. Audit of accounting, budgets, SDK, Scrooge,
  security, ops. PR `fix/accounting-integrity`: S1 complete, S2 RAG-01..08.
  Backend tests 81 → 134; SDK 14 (unchanged). Decisions D1–D4 raised.
  CI green on PR #22 (all five jobs, incl. the backend image build blocked
  locally). Codex review: two valid findings in this PR's own changes
  (archived-rollup overwrite in retention, overlapping entity names) fixed
  with migration 003 + tests.
- 2026-10-28 — Independent review of PR #22: REQUEST CHANGES (1 blocker,
  5 majors, minors). All fixed on the branch with tests (backend 134 → 153);
  details in FINDINGS REV-22.
- 2026-10-28 — Owner decisions D1–D4 recorded. PR `feat/budget-enforcement`
  (stacked on #22): D1 implemented — record-and-flag ingestion (migration
  004), `GET /budgets/check`, SDK `enforce_budgets` / `BudgetExceeded`,
  UI budget actions and event badges. Backend tests 134 → 141; SDK 14 → 22.
- 2026-10-28 — PR #22 merged (squash `106654f`) after independent review,
  fixes, focused re-review (approve with follow-ups) and green CI. PR #23
  ported onto master; it also carries the re-review follow-ups and
  provider/model scopes in the pre-call check. Backend tests → 174; SDK 26.
- 2026-10-28 — Independent re-review of PR #23: REQUEST CHANGES (multi-batch
  reprice left partial rollups; four minors). Fixed with regression tests
  (FINDINGS REV-23). Backend tests → 181.
- 2026-10-28 — Owner: no human code review is available. Merge gate is now an
  independent reviewer agent + green CI + answered bot threads (see CLAUDE.md).
  D4 decided (compatibility-window split). PR `feat/sdk-coverage` (on #23): SDK-01 (most surfaces), SDK-02, SDK-03; SDK tests 22 → 44.
- 2026-10-28 — PR #23 merged (squash `7bcb5c8`) after re-review fixes and green
  CI. Independent review of PR #24: REQUEST CHANGES (one regression: an error
  before `message_start` in `messages.stream()` recorded nothing). Fixed with
  the follow-ups and tests (FINDINGS REV-24); SDK floor raised to Python 3.10.
  PR #24 ported onto master. SDK tests → 64.
- 2026-10-28 — Focused re-review of the #24 fixes: REQUEST CHANGES (closing a
  stream from another task/thread raised and left it open). Fixed with the
  minors (unread/cancelled streams, phantom failures, manager reuse); every
  new test checked against a mutation of its fix (FINDINGS REV-24b). Helper
  streams left early deferred as SDK-13. SDK tests → 73.
- 2026-10-28 — Third review of #24: APPROVE WITH FOLLOW-UPS; follow-ups done
  (tests for two surviving mutations, thread race, clean-EOF close, SDK-13
  wording). SDK tests → 78.
- 2026-10-28 — PR #24 merged (squash `5ed49f6`).
- 2026-10-28 — D2 implemented (`feat/deployment-timezone`): budgets, alert
  dedup, dashboard periods/buckets and Scrooge windows follow the deployment
  time zone; zone validated at setup; `tzdata` added for Windows/slim images.
  Rollup keys stay UTC (ACC-18, deferred). Backend tests → 195.
- 2026-10-28 — Independent review of #25: REQUEST CHANGES (wizard could not
  select most zones; 500 on directory names; Cairo midnight-gap days; Postgres
  bucketing untested in CI). All addressed (FINDINGS REV-25); Postgres service
  added to CI backend-test. Backend tests → 210.
- 2026-10-28 — BUD-06 (`feat/alert-dedup`, stacked on D2): threshold alerts
  keyed by (budget, threshold, period start), migration 005; verified with 8
  racing workers on Postgres. Backend tests → 214.
