# Maç Analiz

R2-first football analytics and paper simulation. It does not place bets or move real money.

## Provider integration status

No provider account or credentials are configured, and no vendor adapter is connected. The app therefore does not invent a live adapter. `create_provider()` returns `PROVIDER_NOT_CONFIGURED` until credentials, scope, and a registered provider adapter are supplied; a configured name without an adapter returns `PROVIDER_UNAVAILABLE`. `PROVIDER_OK` is reported only when the adapter implements and passes `health_check()`.

Phase 4A discovery recommends Sportmonks API 3 as the first single-provider candidate to evaluate, not as a selected or connected source. Its official documentation describes football seasons/fixtures and standard plus Premium pre-match odds. Premium stores opening and subsequent odds changes, but the history is only available until seven days after kickoff; Sportmonks also says historical football data is still being integrated. No official source reviewed verified that its bookmaker list includes İddaa. See [Phase 4A provider discovery](docs/phase4a-provider-discovery.md) for the comparison, historical capability status, request estimate, and live-test blockers.

To implement the live adapter, provide the vendor's official API documentation or a sample contract for:
- league discovery and stable league IDs;
- matches by date/season, individual match lookup, and historical range behavior;
- odds by match, available 1X2 / totals / BTTS markets, bookmaker and snapshot timestamps;
- finalized results, score fields, final-state values and finished timestamps;
- authentication header/query parameter, pagination, rate limits, and retry/Retry-After rules.

Until then, the app uses no production mock data. See provider states below.

## Configuration

Copy .env.example to .env and set only credentials/accounts you actually have:

- PROVIDER_NAME, PROVIDER_BASE_URL, PROVIDER_API_KEY
- PROVIDER_REQUEST_TIMEOUT_SECONDS, PROVIDER_REQUESTS_PER_MINUTE, PROVIDER_MAX_RETRIES
- ENABLED_LEAGUES as comma-separated provider league IDs; ENABLED_SEASONS as comma-separated season IDs
- HISTORICAL_START_DATE to bound historical backfills
- DATABASE_URL for PostgreSQL active state and run ledgers
- R2_ENDPOINT, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET
- TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID for deduplicated daily reports and threshold alerts
- AI settings remain optional and do not change strategy automatically.

Provider status meanings: PROVIDER_NOT_CONFIGURED means credentials/scope/adapter are absent; PROVIDER_UNAVAILABLE means the named adapter cannot be constructed or reached; PROVIDER_RATE_LIMITED means the provider's bounded retry budget was exhausted; PROVIDER_ERROR means a non-retryable adapter/protocol error; PROVIDER_OK requires a successful adapter health check.

## Pipeline commands

Run commands from backend after installing requirements.txt and applying migrations:

- Daily match ingestion: python -m app.jobs.daily_ingest
- Daily match + odds worker: python -m app.worker
- Daily result/archive/cleanup/aggregate run: python -m app.jobs.daily_archive
- Results only: python -m app.jobs.daily_results
- Archive: python -m app.jobs.daily_archive
- Cleanup: python -m app.jobs.daily_cleanup
- Daily metrics: python -m app.jobs.daily_metrics
- Historical backfill: python -m app.jobs.historical_ingest --season 2024-25 --league provider-league-id --start-date 2024-08-01 --end-date 2025-06-30

Historical match and finalized-result partitions are stored as matches/season=<season>/league=<league>/date=YYYY-MM-DD.parquet and results/season=<season>/league=<league>/date=YYYY-MM-DD.parquet, with Zstandard compression, SHA-256 and row-count/key metadata. Odds history is explicitly unavailable unless the configured provider adapter exposes historical snapshots; current snapshots are never rewritten as opening/closing history.

The worker and cron commands write idempotent job_runs with counts and bounded errors. Transient provider timeouts/unavailability/rate limits retry with bounded exponential or Retry-After backoff. Failed and partial runs can be retried; successful/skipped idempotency keys are not reprocessed. PostgreSQL job history retention defaults to 30 days.

PostgreSQL stores active/recent matches, odds, pending predictions/coupons, verified operational results and daily aggregates. Historical datasets belong in R2/Parquet. Cleanup re-reads each R2 object and requires matching key, metadata, SHA-256, row count, persisted VERIFIED state, and a one-day buffer. Match cleanup waits until dependent odds/results/predictions/coupon items have been removed safely.

Telegram sends an aggregated daily summary and a deduplicated PostgreSQL storage alert; ingestion messages are grouped by configured leagues/date. No per-odds notifications are sent. Missing R2 or Telegram credentials are reported as not configured.

## Verification

- Backend: py -m pytest -q
- Frontend: npm run typecheck and npm run build
- Migration SQL: alembic upgrade head --sql
- Compose config: docker compose config -q
- Render Blueprint: JSON Schema validation; no deployment is automatic.
