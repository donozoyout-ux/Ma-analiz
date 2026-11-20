# Maç Analiz

R2-first football analytics and paper simulation. It does not place bets or move real money.

## Provider integration

Goaloo is the configured primary provider for daily fixtures, results, and observed bookmaker odds. Sportmonks remains an optional adapter. `/api/v1/health` reports provider state and configured league/season scope; it does not expose credentials. Configure only league IDs confirmed by provider discovery.

See [Phase 4A provider discovery](docs/phase4a-provider-discovery.md) and [Phase 4B integration report](docs/phase4b-integration.md) for verified provider limitations.

### Bookmaker odds and backfill integrity

Odds snapshots retain provider source, bookmaker entity, market, selection, optional canonical numeric line plus original line text, capture timestamp, period, and status. Match detail projects bookmaker-specific Opening → Latest → Closing from observed valid pre-match snapshots. Closing is withheld until the provider marks the match started, considers only snapshots no later than kickoff, and never reads `LIVE` odds. Live rows are shown separately. If no qualifying source records exist the panel says “Veri bekleniyor”.

`GET /api/v1/provider/leagues` and `GET /api/v1/provider/leagues/{league_id}/seasons` discover provider competitions/seasons; unconfigured or unavailable providers return an explicit 503 error code. `GET /api/v1/bookmakers` returns only bookmakers actually observed and stored. No bookmaker is renamed İddaa.

Historical ingestion persists league/season/day checkpoints, failed record IDs and per-match field coverage. Goaloo technical statistics are read from the match's observed HTML statistics section; missing values remain missing. Historical odds collection starts at 2025-26 and requires permitted source access. Goaloo's robots policy currently blocks its AJAX odds endpoint, which produces `PROVIDER_ACCESS_RESTRICTED`; latest and closing prematch timing is never inferred. Sportmonks' published odds history retention is seven days, so a token alone does not provide a complete old-season archive. See [R2 archive operation and validation](docs/r2-historical-archive.md).

## Configuration

Copy `.env.example` to `.env` and configure only accounts you have:

- Provider: `PROVIDER_NAME=goaloo`, `GOALOO_BASE_URL`, `GOALOO_ODDS_BASE_URL`, `PROVIDER_REQUEST_TIMEOUT_SECONDS`, `PROVIDER_REQUESTS_PER_MINUTE`, `PROVIDER_MAX_RETRIES`.
- Scope for scheduled ingestion: `ENABLED_LEAGUES` and `ENABLED_SEASONS`, comma-separated provider IDs. The example archive seasons start at `2023-2024`.
- PostgreSQL active state and job ledgers: `DATABASE_URL` (the existing app connection setting).
- Cloudflare R2: `R2_ENDPOINT`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET`, `R2_REGION` (`auto` for Cloudflare R2).
- Telegram: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`; optional for this phase.

Never put credentials in source control, reports, or screenshots. Provider failures use explicit codes such as `PROVIDER_NOT_CONFIGURED`, `PROVIDER_AUTH_ERROR`, `PROVIDER_RATE_LIMITED`, `PROVIDER_UNAVAILABLE`, and `PROVIDER_INVALID_RESPONSE`; failed calls are not converted to successful empty datasets.

## One-day smoke test

Install `backend/requirements.txt`, set `DATABASE_URL` and R2 credentials if you want to verify writes, then select a date from actual account coverage and run from `backend`:

For the R2 archive task, first run `python -m app.jobs.r2_smoke_test --parquet`, then `python -m app.jobs.historical_probe --league 36 --season 2025-2026 --date 2025-08-15`. The probe is source-only and writes neither SQL nor R2. A persisted one-day backfill requires the same league/season enabled in the environment and an applied database migration. Complete that smoke test before starting a bulk scope.

For rollout without historical odds, run `python -m app.jobs.archive_preflight` for a secret-safe configuration report, then (only with configured connections and scope) `python -m app.jobs.archive_preflight --smoke --league 36 --season 2025-2026 --date 2025-08-15`. This checks PostgreSQL read-only, R2 Parquet round-trip and the real day's archives, and never invokes cleanup or bulk ingestion. `historical_backfill --skip-odds` or `HISTORICAL_COLLECT_ODDS=false` lets matches/results/statistics complete independently; a later odds-enabled run can resume the same day.

Without Render Shell, temporarily replace the existing web service's Start Command with `cd backend && python -m app.jobs.archive_diagnostic_service --mode connections`, then `--mode smoke` after connection checks pass. Clear any old automatic migration Pre-Deploy Command first. No extra service is created; the normal web UI is temporarily replaced by diagnostics. See [the complete no-Shell procedure and rollback](docs/render-free-archive-diagnostics.md). Do not apply the full paid-plan Blueprint to a Free service for this test.

```text
python -m app.jobs.provider_smoke_test --date YYYY-MM-DD
```

This performs bounded read-only discovery and checks one date, reporting normalized/rejected matches, odds markets/bookmakers and timestamp capabilities, results, errors, and request latency. It does not pick a fictitious date or start a backfill. To also migrate and upsert only that league/date in PostgreSQL, verify duplicate behavior and result settlement idempotency, and write/verify the date's match, odds, and result Parquet partitions in R2, pass `--persist`:

```text
python -m app.jobs.provider_smoke_test --date YYYY-MM-DD --persist
```

`--persist` never invokes cleanup. It reports database/R2 failures without claiming success. Goaloo does not require an API token; a Sportmonks smoke test requires its account token. A persistence run additionally needs reachable PostgreSQL and R2 credentials.

## Pipeline commands

Run commands from `backend` after installing requirements and applying migrations:

- Daily match ingestion: `python -m app.jobs.daily_ingest`
- Daily match + odds worker: `python -m app.worker`
- Results/archive/cleanup/aggregate run: `python -m app.jobs.daily_archive`
- Results only: `python -m app.jobs.daily_results`
- Archive: `python -m app.jobs.daily_archive`
- Cleanup: `python -m app.jobs.daily_cleanup`
- Daily metrics: `python -m app.jobs.daily_metrics`
- Historical match/result backfill: `python -m app.jobs.historical_backfill --dry-run` to review configured scopes, then run without `--dry-run` to start; supports `--league`, `--season`, `--start-date`, `--end-date`, and `--batch-size`. It is operator-started and resumable, not run during web or worker startup. See [production data architecture](docs/production-data-architecture.md).
- One-scope historical ingestion: `python -m app.jobs.historical_ingest --season 2024-2025 --league provider-league-id --start-date 2024-07-01 --end-date 2025-06-30`

Historical matches and finalized results use R2 Parquet partitions with Zstandard compression, SHA-256 and row-count/key metadata. Daily archiving processes every configured season. Cleanup re-reads each object and requires matching metadata, checksum, row count, persisted `VERIFIED` state, and a one-day buffer. It does not remove data before a verified archive. PostgreSQL active matches and odds are short-lived operational state (14 and 30 days by default); these jobs do not remove R2 archive objects. Provider daily jobs skip safely until credentials and league scope are configured.

## Verification

- Backend: `py -m pytest -q`
- Frontend: `npm run typecheck` and `npm run build` from `frontend`
- Migration SQL: `alembic upgrade head --sql` from `backend`
- Compose config: `docker compose config -q`
- Render Blueprint: validate `render.yaml` against Render's schema; this does not deploy.
