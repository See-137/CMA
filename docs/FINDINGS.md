# CMA finding register

Every issue or improvement found while auditing CMA, with the action that resolves it,
its priority, the stage it belongs to (see [PLAN.md](PLAN.md)), and how resolution is
verified. Deferred items state why and what would bring them back.

**Priorities:** P0 wrong money / lost data / broken core path · P1 significant ·
P2 moderate · P3 minor.
**Evidence:** *Verified* = reproduced by running code (test or script) ·
*Inferred* = from reading code, not yet reproduced · *Claim* = documentation only.
**Status:** Done (commit) · Open · Decision (blocked on an owner decision, see PLAN.md) ·
Deferred.

Audit baseline: `master` @ `3c0e648` (2026-10-28). Reproduction scripts for the SDK
and Scrooge audits were run in a scratch area outside the repo; the behaviours they
demonstrated are encoded as regression tests where fixed.

## Accounting and attribution (ACC)

| ID | P | Finding | Evidence | Action | Stage | Verification | Status |
|---|---|---|---|---|---|---|---|
| ACC-01 | P0 | SDK sends `provider="openai"`/`"anthropic"`, seeded providers are `"OpenAI"`/`"Anthropic"`; exact-match lookup priced every auto-instrumented call at $0. Dated snapshots and `model_alias` never matched. | Verified | Case-insensitive provider name/type match; model by exact, alias list, ci name, date-snapshot fallback. Rules in `cost_engine.py` docstring. | S1 | `tests/test_pricing.py`; live SDK → server run priced `gpt-4o` $3.50, `claude-sonnet-4-6` $0.75 | Done `c348570` |
| ACC-02 | P0 | Offset timestamps stored as wall-clock: SQLite dropped the offset (5 h error), Postgres converted to the server session zone. | Verified (SQLite + PG16) | Normalise to naive UTC in `EventIn` and list filters. | S1 | `test_tz_aware_timestamp_is_stored_as_utc`, `test_event_list_date_filter_accepts_offsets` | Done `ec49f62` |
| ACC-03 | P0 | Concurrent first events for a new agent raced the `agents.name` UNIQUE insert; losers' events silently dropped with HTTP 200 (PG: 8 requests → 2 rows). | Verified (PG16) | Savepoint around agent INSERT, re-read on `IntegrityError`; `errored` count in ingest response. | S1 | `test_agent_autodiscovery_race_does_not_drop_event`; PG repro 8/8 stored | Done `80efb47` |
| ACC-04 | P1 | Unpriced events were indistinguishable from free ones. | Verified | `cost_events.cost_source` (`pricing`/`client`/`unpriced`, migration 002); `unpriced_events` on dashboard overview; UI warning. | S1 | `test_cost_source_is_recorded`, `test_dashboard_overview_counts_unpriced` | Done `414dcab`, `d9fe6ef` |
| ACC-05 | P1 | No way to recover history stored at $0 once pricing is fixed. | Verified | `POST /admin/reprice` (admin, date range, dry run): reprices unpriced + legacy $0-with-tokens rows, recomputes affected rollups, re-embeds; Settings button. Client costs untouched. | S1 | `test_reprice_fixes_unpriced_and_legacy_rows` | Done `414dcab`, `d9fe6ef` |
| ACC-06 | P1 | Retention deleted events that arrived after their day was rolled up, so that spend vanished from all totals; days with events but no rollup were never pruned. A first fix (recompute before prune) could overwrite an already-pruned day's rollup with only late events (Codex review on PR #22). | Verified | Never-pruned days: recompute, mark rollups `archived` (migration 003, backfilled for days with no remaining events), delete in one transaction. Archived days: late events are *added* to the archived rows; `compute_daily_rollups` never recomputes an archived day. | S1 | `tests/test_retention.py` (3 cases); migration 003 backfill checked on PG + SQLite | Done |
| ACC-07 | P1 | `reset-full` deleted pricing; seeding only runs at startup → $0 events until restart. | Inferred → Verified by test | Re-seed in `reset-full`. | S1 | `test_full_reset_restores_default_pricing` | Done `50d6fbd` |
| ACC-08 | P0 | Anthropic prompt-cache tokens dropped: `input_tokens=20, cache_creation=500, cache_read=50000` recorded as 20 input tokens. Pricing model has no cache rates. | Verified (SDK repro) | Capture cache read/write tokens in SDK; store and price them server-side (schema + pricing change). | S4 | Fixture usage → expected cost incl. cache write/read prices | Decision D3 |
| ACC-09 | P1 | OpenAI cached input billed at full input price (`cached_tokens` ignored). | Verified (SDK repro) | Same mechanism as ACC-08. | S4 | `prompt_tokens=10000, cached=9000` → discounted cost | Decision D3 |
| ACC-10 | P1 | No idempotency: a timeout after the server committed makes the SDK resend, storing the event twice. | Verified (SDK repro) | Optional `event_id` on `EventIn` (unique, insert-or-ignore, `duplicates` count); SDK generates uuid4. Additive. | S4 | Hang-then-timeout repro stores exactly 1 row | Open |
| ACC-11 | P2 | Seed price table is stale (no `gpt-4o-mini`, newer OpenAI/Google models); prices are hand-maintained. | Inferred | Refresh seed from the vendors' official pricing pages; document "last verified" date. Seed only affects fresh installs; existing installs edit in UI then reprice. | S6 | Each seeded price matches the official page on the stated date | Open (needs official-page access) |
| ACC-12 | P3 | Money stored as float, rounded to 8 dp. | Inferred | None now. | — | — | Deferred: error is far below display precision at current scale. Trigger: invoice reconciliation feature or a measured discrepancy > $0.01. |
| ACC-13 | P1 | API datetimes were naive ISO strings; browsers parse them as local time, shifting every "x ago" and chart label by the viewer's offset. Timeseries format differed SQLite vs PG. | Verified | Serialise all datetimes with `Z`; uniform bucket format; day buckets labelled in UTC. | S1 | `test_api_datetimes_are_explicit_utc` | Done `1967fd6` |
| ACC-14 | P2 | Dashboards and Scrooge read raw events only; after retention, older history disappears from both (rollups exist but are unused). | Inferred | For days before the oldest retained event, read rollups; never sum both for one day. | S6 | Prune, then dashboard/Scrooge totals for the pruned range are unchanged | Open |
| ACC-15 | P2 | Dashboard change % compared period-to-date with the whole previous period; `quarter` compared with yesterday; inclusive boundary. | Verified | Like-for-like previous window, half-open. | S1 | `test_prev_period_is_like_for_like` | Done `39f42a1` |
| ACC-16 | P3 | `SetupConfig.currency` is collected but unused; everything is USD. | Verified (grep) | Either remove the choice or label it "display only"; costs remain USD. | S6 | Setup/UI no longer implies conversion | Open |

## Budgets and alerts (BUD)

| ID | P | Finding | Evidence | Action | Stage | Verification | Status |
|---|---|---|---|---|---|---|---|
| BUD-01 | P0 | UI offers "Block Requests"/"Throttle" (`block`/`throttle`); enforcement only recognises `stop`/`rate_limit`/`model_downgrade`. Budgets created in the UI never enforce. | Verified | Align UI and backend values once D1 is decided. | S3 | Budget created through each UI option behaves as documented | Decision D1 |
| BUD-02 | P0 | `stop` enforcement rejects *ingestion* of events after the limit. The LLM call already happened, so the spend is real but not recorded: dashboards, budgets and Scrooge stop counting while money keeps going out. README says CMA can "stop them before the call is made"; nothing does that. | Verified | Per D1: always record; enforce before the call (SDK pre-call check) and/or flag over-budget events. | S3 | Over-limit events are stored and visible; pre-call check blocks the next call when enabled | Decision D1 |
| BUD-03 | P1 | Budget fields were free strings: `scope="agnet"` became global, unknown period became daily, agent scope without `scope_ref` became global, negative limits accepted. | Verified | Enum/range validation on create/update (422); existing rows still readable. | S1 | `test_invalid_budget_rejected`, `test_update_cannot_strip_scope_ref` | Done `ebce1e1` |
| BUD-04 | P1 | All period boundaries (budgets, dashboard, rollups, Scrooge) are UTC days; the time zone captured at setup is never used. For UTC+2/+3 "today" starts at 02:00/03:00 local. | Verified (grep) | Per D2. | S3 | Event at 23:30 local lands in the local day (if D2 = deployment tz) or UI labels UTC (if D2 = UTC) | Decision D2 |
| BUD-05 | P2 | Enforcement is per-request and non-atomic: concurrent requests can both pass, and the event that crosses the limit is accepted. | Inferred | Document the guarantee as a soft limit (after D1); atomic accounting not planned. | S3 | Docs state the guarantee | Open |
| BUD-06 | P2 | `check_budgets` runs a full SUM per budget on every ingest request in background; concurrent runs can create duplicate threshold alerts (dedup is a message-substring lookup). | Inferred | Unique key on (budget, threshold, period start); evaluate only budgets touched by the batch. | S3 | Concurrent ingestion produces one alert per threshold per period | Open |

## Instrumentation SDK (SDK)

| ID | P | Finding | Evidence | Action | Stage | Verification | Status |
|---|---|---|---|---|---|---|---|
| SDK-01 | P0 | Uninstrumented surfaces record nothing: OpenAI `responses.create`, `embeddings.create`, `chat.completions.parse`, `with_raw_response` (stream silently), Anthropic `beta.messages.create`, `messages.parse`, async `messages.stream`, async legacy completions, async embeddings. | Verified (SDK repro, openai 3.40 / anthropic 2.3) | Patch each surface (Responses usage from `response.completed`); add to pristine registry. | S4 | Parametrised test: every surface, sync+async → exactly one event with fake usage | Open |
| SDK-02 | P1 | Injected `stream_options.include_usage` adds a `choices=[]` chunk; common `chunk.choices[0]` loops raise `IndexError` only when CMA is active. Contradicts "never alters the call". | Verified | When CMA injected the option, consume the usage-only chunk without yielding it. | S4 | Same loop succeeds with CMA and records 7/3 tokens | Open |
| SDK-03 | P1 | Early `break` from a stream, or a user exception inside `messages.stream()`, records `failure` with 0 tokens even after usage was received. | Verified | Treat `GeneratorExit` as partial, keep collected usage; `failure` only for API errors. | S4 | Early-exit repros record collected tokens | Open |
| SDK-04 | P1 | LangChain callback + patched client both record each call (2 events). | Verified | Handler defers when the provider client is patched (contextvar). | S4 | One `invoke` → 1 event | Open |
| SDK-05 | P1 | LangChain handler ignores `usage_metadata` and `ls_model_name`/`ls_provider` (modern chat models log `unknown/openai/0/0`); global registration is dead on langchain-core 1.x. | Verified | Read `usage_metadata` first, LangSmith metadata for model/provider; drop or fix global registration. | S4 | Anthropic-shaped LC response logs correct model/tokens | Open |
| SDK-06 | P1 | Worker thread does not exist in forked children (gunicorn preload, Celery prefork): child events never sent. | Verified | `os.register_at_fork` to rebuild lock/client/worker in child. | S4 | Fork repro delivers child events | Open |
| SDK-07 | P1 | One non-JSON-serialisable event (datetime in metadata, NaN cost) drops its whole chunk. | Verified | Sanitise in `log_event`; isolate bad events. | S4 | 10 good + 1 bad → 10 delivered | Open |
| SDK-08 | P2 | Plain `CostTracker` has no exit flush (script exit loses events); `close()` can take ~21 s against a hung backend. | Verified | Weakref `atexit` hook; total close budget (~5 s). | S4 | Exit-without-close repro delivers; hung backend close ≤ budget | Open |
| SDK-09 | P2 | HTTP 413 retries the same chunk size forever. | Verified | Halve chunk on 413 down to 1, then drop and count. | S4 | 413 repro makes progress | Open |
| SDK-10 | P2 | Auto-instrumented spend all lands on agent `"auto"`; `trace_request()` does not reach patched calls; no workflow/swarm defaults. | Inferred | Contextvar `tracker.context(agent=, workflow=, trace_id=)` read by wrappers; defaults on `auto_instrument`. | S4 | Calls inside a context carry its attribution | Open |
| SDK-11 | P2 | SDK ignores `rejected`/`errored` in the ingest response, so dropped events count as sent. | Inferred | Parse response; expose in `stats`; warn. | S4 | Stats reflect server-side rejections | Open |
| SDK-12 | P3 | `InstrumentedStream` is not an `openai.Stream`; post-call bookkeeping not exception-guarded; cancellation not recorded; error text uncapped. | Verified/Inferred | Guard bookkeeping; cap error length; document isinstance limitation. | S4 | Bookkeeping exception never reaches caller | Open |

## Scrooge (RAG)

| ID | P | Finding | Evidence | Action | Stage | Verification | Status |
|---|---|---|---|---|---|---|---|
| RAG-01 | P0 | Totals came from a top-20 group-by with no grand total (30 × $1 → $20 in prompt). | Verified | Separate SUM row per window as the only `total`; breakdown + remainder row. | S2 | `test_total_is_exact_beyond_breakdown_limit` | Done `64e05d9` |
| RAG-02 | P0 | Inclusive window end: midnight event counted in two days. | Verified | Half-open windows everywhere. | S2 | `test_midnight_event_counts_in_exactly_one_day` | Done `64e05d9` |
| RAG-03 | P0 | First-substring time parsing; unrecognised periods silently became all-time; applied window never told to the LLM. | Verified | Whole-word longest-first parsing, per-window totals, explicit window/ALL TIME/not-understood lines. | S2 | `test_time_phrases`, `test_unrecognised_period_is_flagged_not_answered_as_all_time`, `test_comparison_gets_a_total_per_window` | Done `64e05d9` |
| RAG-04 | P0 | Substring entity matching (`chatbot gpt-4o` also matched `bot`, `gpt-4`); unpriced models unfilterable. | Verified | Whole-token matches with overlapping spans resolved longest-first (both names kept when each has its own occurrence — Codex review on PR #22); include names present on events. | S2 | `test_entity_matching_is_whole_token_longest_wins`, `test_independently_named_overlapping_entities_are_all_kept`, `test_unpriced_model_is_filterable_and_provider_spellings_merge` | Done `64e05d9` |
| RAG-05 | P1 | Ingested names (writable by any ingest-key holder) placed in a system-role message. | Verified | User-role `<data>` block declared untrusted by the system prompt. | S2 | `test_ingested_names_never_reach_a_system_message` | Done `64e05d9` |
| RAG-06 | P1 | Combined queries silently dropped comparison/timeline rows past a shared 20-row cut while still citing them; timeline kept the oldest buckets. | Verified | No shared cut; per-section caps; most recent buckets; daily granularity > 3 days. | S2 | Prompt contains every retrieved section | Done `64e05d9` |
| RAG-07 | P2 | Embedding/vector failure returned 500 (Chroma is documented as a cache). | Verified | Degrade to SQL with a note. | S2 | `test_vector_store_failure_degrades_to_sql`, `test_chat_endpoint_survives_vector_failure` | Done `64e05d9` |
| RAG-08 | P2 | No-data path gave the model nothing to say "no data" from; "no disclaimers" rule discouraged saying so. | Verified | Explicit `NO MATCHING DATA`; prompt rule that stating missing data is required. | S2 | `test_no_matching_data_is_explicit` | Done `64e05d9` |
| RAG-09 | P1 | Citations returned to the UI are "what was retrieved", not what the answer used; labels in the answer are not checked against the context. | Verified | Parse answer labels; flag/strip labels absent from the data block; return only cited sources. | S2 | Answer citing a non-existent label is flagged | Open |
| RAG-10 | P1 | Vector records carry no timestamp and are not time-filtered; event and rollup records of the same spend sit side by side. | Inferred | Timestamp in embedded text + epoch metadata filter; prompt already forbids summing vector records. | S2 | "yesterday" question retrieves only that window's records | Open |
| RAG-11 | P2 | Chat history from the client is unvalidated (forged assistant turns, 200 k-char messages). | Verified | `role` literal, per-message and total length caps. | S5 | Oversized/invalid history → 422 | Open |
| RAG-12 | P2 | Semantic search `top_k` and `query` unbounded. | Verified | `top_k` 1–100, query length cap. (Offset date filters fixed in `1967fd6`.) | S5 | Out-of-range → 422 | Open |
| RAG-13 | P2 | `/chat/status` always reports the vector store as ready. | Verified | Report real status. | S6 | Status reflects a failing store | Open |
| RAG-14 | P1 | Answer quality with a real LLM is unmeasured (README roadmap: golden set). | Claim | Golden questions over a fixed dataset; assert retrieved context deterministically in CI, LLM answers in an opt-in job. | S2 | CI job green; opt-in report | Open — LLM half **blocked** here (no model access) |

## Security, auth, privacy (SEC)

| ID | P | Finding | Evidence | Action | Stage | Verification | Status |
|---|---|---|---|---|---|---|---|
| SEC-01 | P1 | One shared, non-rotatable key is both the SDK ingest credential and the management credential (returned by `/auth/login`); it can edit pricing, budgets, webhooks and read all data. | Verified | Per D4. | S5 | Ingest key gets 403 on management writes (if chosen) | Decision D4 |
| SEC-02 | P2 | Passwords > 72 bytes → 500 (bcrypt 5); setup accepts empty password / any email. | Verified | Length/policy validation, 422 not 500. | S5 | Tests for long/empty passwords | Open |
| SEC-03 | P2 | Setup is check-then-insert; concurrent first-run POSTs can create two configs. | Inferred | Single-row constraint + conflict handling. | S5 | Concurrent setup → one row | Open |
| SEC-04 | P2 | Behind a host reverse proxy (systemd deploy) all clients share one rate-limit bucket: 10 bad logins/min locks everyone out. | Inferred | Document/configure `--proxy-headers --forwarded-allow-ips`; per-account failure limit. | S5 | Config + test for per-account limit | Open |
| SEC-05 | P2 | SSRF guard: DNS-rebinding TOCTOU (validated then re-resolved by httpx); `100.64.0.0/10` allowed. | Verified (CGNAT) / Inferred (rebinding) | Block `not ip.is_global`; connect to the validated address. | S5 | Tests for CGNAT and pinned resolution | Open |
| SEC-06 | P2 | Event `metadata` uncapped (5 MB accepted); nginx default 1 MB body limit can reject large SDK batches. | Verified | Cap metadata size; align body limits; document that metadata is stored in plaintext. | S5 | Oversize metadata → 422; 1000-event batch passes nginx | Open |
| SEC-07 | P3 | Webhook/Slack URLs (secrets) logged by httpx/alert service and returned by `GET /alerts/channels`. | Inferred/Verified | Lower httpx log level, redact URLs, mask config in responses. | S5 | Log/response contain no full webhook URL | Open |
| SEC-08 | P3 | Non-ASCII bearer token → 500 (`compare_digest` on str). | Verified | Compare bytes; 401. | S5 | Test | Open |
| SEC-09 | P3 | `/docs` public; Grafana defaults to admin/admin (loopback); data sent to OpenAI when `LLM_PROVIDER=openai` not documented as a privacy consideration. | Verified | Document; optionally gate `/docs`. | S6 | Docs updated | Open |

## Operations, docs, UX (OPS / DOC / UX)

| ID | P | Finding | Evidence | Action | Stage | Verification | Status |
|---|---|---|---|---|---|---|---|
| OPS-01 | P2 | `/admin/export` is described as a backup but holds the latest 1000 events only; no backup/restore procedure is documented. | Verified | Rename to summary export; add a backup/restore runbook (pg_dump + Chroma rebuild). | S6 | Runbook exercised on a scratch Postgres | Open |
| OPS-02 | P3 | `/admin/status` DB size reads a hard-coded `data/cma.db` (shows 0 MB for any other SQLite path and for Postgres). | Verified (screenshot) | Derive from `DATABASE_URL`; PG size query. | S6 | Test | Open |
| OPS-03 | P2 | A local uvicorn did not exit within ~5 s of SIGTERM (observed once; cause unknown). | Observed once | Reproduce; check maintenance task/Chroma shutdown. | S6 | Clean exit < 5 s | Open (investigate) |
| OPS-04 | P3 | Frontend ships a single 700 kB chunk. | Verified (build warning) | Route-level code splitting. | — | — | Deferred: no load-time complaints for a self-hosted dashboard. Trigger: remote/mobile use or a reported slow load. |
| DOC-01 | P1 | README claims: SDK "never alters or breaks the underlying call" (SDK-02), "stop them before the call is made" / "hard-stop enforcement" (BUD-02). | Verified | Wording corrected in this PR to describe actual behaviour; revisit after D1/SDK-02. | S1/S3 | README matches tested behaviour | Done (interim) |
| DOC-02 | P3 | Test counts and gotchas in CLAUDE.md/README stale (89 tests; exact-match pricing; substring entity matching). | Verified | Updated. | S1 | Counts match `pytest` | Done |
| UX-01 | P2 | Events page does not show `cost_source`; unpriced rows look free. | Verified | Badge for unpriced/client costs. | S6 | Visual check | Open |
| UX-03 | P3 | Change % shows "+100%" when the previous window had no data (no baseline), which reads as a real doubling. | Verified (screenshot) | Return null when there is no baseline; UI shows "new". | S6 | Overview with empty previous window shows no percentage | Open |
| UX-02 | P3 | No frontend tests. | Verified | Vitest smoke tests for formatters and the unpriced banner. | — | — | Deferred: build + type-check cover current changes. Trigger: next non-trivial UI change. |
