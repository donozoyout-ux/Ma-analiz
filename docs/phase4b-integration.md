# Phase 4B — Sportmonks adapter and live-test readiness

Checked locally: 2026-11-16. No provider credentials or live storage credentials were supplied in `.env`.

## Implementation

- Added a Sportmonks API 3 provider adapter for authenticated API access, paginated league discovery, league season discovery, fixture lookup by date/ID, Premium odds snapshots, and final fixture results.
- The adapter keeps provider payload handling in the provider package, applies bounded retries/rate limiting through the existing caller, classifies authentication/rate/network/protocol failures, and does not include the token in URLs or errors.
- The one-day command dynamically resolves Turkish Süper Lig and its season IDs. `--persist` is scoped to that league/date, runs existing PostgreSQL migration/upsert/result paths and verifies R2 Parquet metadata, checksum, row count, and readback. It never runs cleanup or backfill.
- Active odds preserve provider market and bookmaker identifiers. Historical result queries respect `verified_at`; provider-only season fields are filtered at the active SQL persistence boundary.
- The dashboard health panel displays provider state, latency, last successful request, and configured scope. `ONLINE` requires a successful provider health request.

## Verified locally

| Check | Result |
|---|---|
| Backend tests | **42 passed**, 1 dependency deprecation warning |
| Frontend typecheck | PASS |
| Frontend production build | PASS |
| Alembic offline SQL through `0003_provider_market_ids` | PASS |
| FastAPI import | PASS |
| `docker compose --env-file .env.example config -q` | PASS |
| Render Blueprint JSON Schema | PASS |
| `git diff --check` | PASS |
| Docker Engine runtime | `DOCKER_RUNTIME_NOT_VERIFIED` — Docker Desktop Linux engine pipe is unavailable |

The provider unit tests use mocked HTTP transport only; they are not provider data. The no-credential smoke-command path returned `PROVIDER_NOT_CONFIGURED` without attempting a network request. No date was verified against provider coverage.

## Live checks and capability

**LIVE SMOKE TEST BLOCKED.** No Sportmonks token/account was configured, so authentication, health latency, Turkish league ID, season ID, date coverage, normalized match counts, odds, results, markets, bookmakers, snapshots, and provider quota were not observed. The checked-in date argument is user-supplied at execution; the local block-path check is not a real smoke date.

- PostgreSQL: `POSTGRES_NOT_CONFIGURED`; no connection, migration against a live server, ingestion, or database idempotency values were observed.
- R2: `R2_NOT_CONFIGURED`; no upload, HEAD, checksum, row count, or readback against Cloudflare was observed.
- Results and settlement: code path is wired to existing final-result ingestion/settlement, but provider result completeness and live idempotency are unverified.
- Historical matches/results by season (`2024-25`, `2025-26`, `2026-27`): not queried; subscription coverage is unknown.
- Sportmonks documents Premium odds history/opening values for seven days after kickoff. This documented retention is insufficient for a full 2024-25 or 2025-26 historical odds backfill. Premium odds entitlement is account/add-on dependent; actual Süper Lig market/bookmaker availability remains unverified. No historical odds have been reconstructed.
- Official rate limit is plan/endpoint dependent; account quota and runtime cannot be calculated without a real response and plan.
- Request counts, historical match/odds counts, and compressed Parquet sizes cannot be responsibly estimated without a real sample. No historical backfill or deployment was run.
- Odds source label is `SPORTMONKS`, with actual provider bookmaker name/IDs when returned. No evidence verifies an İddaa source.

## Run after credentials are configured

From `backend`, first run a one-date read-only check using a date selected from the actual Sportmonks account coverage:

```text
python -m app.jobs.provider_smoke_test --date YYYY-MM-DD
```

Only after reviewing that output, use `--persist` for a single-date PostgreSQL/R2 verification. Do not launch historical odds backfill with Sportmonks: the documented seven-day retention does not cover prior full seasons.

## Deployment

`NOT PERFORMED`.
