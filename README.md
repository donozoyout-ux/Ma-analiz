# Maç Analiz

R2-first football analytics and paper simulation. It does not place bets or move real money.

## Provider integration

Sportmonks API 3 is the first implemented provider adapter. Authentication uses the `Authorization` header. The app resolves Turkish Süper Lig and its seasons from the account's league catalogue; it does not assume league or season IDs. `/api/v1/health` shows provider state, latency, last successful API request, and configured league/season scope. `ONLINE` is returned only after the provider health endpoint succeeds. Tokens are never included in health responses or adapter errors.

The adapter supports league/season discovery, fixtures by date and ID, Premium fixture odds, and finalized fixture results. Odds are labeled `SPORTMONKS` and retain the provider's actual bookmaker name and IDs. They are not labeled İddaa. Sportmonks documents Premium odds history as available only through seven days after kickoff, so this provider cannot supply full-season historical odds for 2024-25 or 2025-26. Do not start a historical odds backfill on that assumption. Premium odds access is account/add-on dependent; verify account coverage before relying on it.

Official contract references: [authentication](https://docs.sportmonks.com/v3/welcome/authentication), [leagues](https://docs.sportmonks.com/v3/endpoints-and-entities/endpoints/leagues/get-all-leagues), [fixtures by date](https://docs.sportmonks.com/v3/endpoints-and-entities/endpoints/fixtures/get-fixtures-by-date), [fixture by ID](https://docs.sportmonks.com/v3/endpoints-and-entities/endpoints/fixtures/get-fixture-by-id), [Premium odds by fixture](https://docs.sportmonks.com/v3/endpoints-and-entities/endpoints/premium-odds-feed/premium-pre-match-odds/get-premium-odds-by-fixture-id), and [scores](https://docs.sportmonks.com/v3/tutorials-and-guides/tutorials/includes/scores).

See [Phase 4A provider discovery](docs/phase4a-provider-discovery.md) and [Phase 4B integration report](docs/phase4b-integration.md) for verified limitations and current test status.

## Configuration

Copy `.env.example` to `.env` and configure only accounts you have:

- Provider: `PROVIDER_NAME=sportmonks`, `SPORTMONKS_API_TOKEN`, `SPORTMONKS_BASE_URL`, `SPORTMONKS_TIMEOUT_SECONDS`, `SPORTMONKS_MAX_RETRIES`, `PROVIDER_REQUESTS_PER_MINUTE`.
- Scope for scheduled ingestion: `ENABLED_LEAGUES` and `ENABLED_SEASONS`, comma-separated provider IDs. The one-day smoke command discovers Turkish Süper Lig IDs dynamically and does not require these values.
- PostgreSQL active state and job ledgers: `DATABASE_URL` (the existing app connection setting).
- Cloudflare R2: `R2_ENDPOINT`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET`, `R2_REGION` (`auto` for Cloudflare R2).
- Telegram: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`; optional for this phase.

Never put credentials in source control, reports, or screenshots. Provider failures use explicit codes such as `PROVIDER_NOT_CONFIGURED`, `PROVIDER_AUTH_ERROR`, `PROVIDER_RATE_LIMITED`, `PROVIDER_UNAVAILABLE`, and `PROVIDER_INVALID_RESPONSE`; failed calls are not converted to successful empty datasets.

## One-day smoke test

Install `backend/requirements.txt`, set `DATABASE_URL` and R2 credentials if you want to verify writes, then select a date from actual account coverage and run from `backend`:

```text
python -m app.jobs.provider_smoke_test --date YYYY-MM-DD
```

This performs bounded read-only discovery and checks one date, reporting normalized/rejected matches, odds markets/bookmakers and timestamp capabilities, results, errors, and request latency. It does not pick a fictitious date or start a backfill. To also migrate and upsert only that league/date in PostgreSQL, verify duplicate behavior and result settlement idempotency, and write/verify the date's match, odds, and result Parquet partitions in R2, pass `--persist`:

```text
python -m app.jobs.provider_smoke_test --date YYYY-MM-DD --persist
```

`--persist` never invokes cleanup. It reports database/R2 failures without claiming success. A live smoke test needs a real provider token; a persistence run additionally needs reachable PostgreSQL and R2 credentials. No live API, database, or R2 test has been recorded in the Phase 4B report unless explicitly marked as performed there.

## Pipeline commands

Run commands from `backend` after installing requirements and applying migrations:

- Daily match ingestion: `python -m app.jobs.daily_ingest`
- Daily match + odds worker: `python -m app.worker`
- Results/archive/cleanup/aggregate run: `python -m app.jobs.daily_archive`
- Results only: `python -m app.jobs.daily_results`
- Archive: `python -m app.jobs.daily_archive`
- Cleanup: `python -m app.jobs.daily_cleanup`
- Daily metrics: `python -m app.jobs.daily_metrics`
- Historical match/result ingestion (not odds): `python -m app.jobs.historical_ingest --season 2024-25 --league provider-league-id --start-date 2024-08-01 --end-date 2025-06-30`

Historical matches and finalized results use R2 Parquet partitions with Zstandard compression, SHA-256 and row-count/key metadata. Cleanup re-reads each object and requires matching metadata, checksum, row count, persisted `VERIFIED` state, and a one-day buffer. It does not remove data before a verified archive. Provider daily jobs skip safely until credentials and league scope are configured. Job records are idempotent and retained for 30 days by default.

## Verification

- Backend: `py -m pytest -q`
- Frontend: `npm run typecheck` and `npm run build` from `frontend`
- Migration SQL: `alembic upgrade head --sql` from `backend`
- Compose config: `docker compose config -q`
- Render Blueprint: validate `render.yaml` against Render's schema; this does not deploy.
