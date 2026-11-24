# Historical data pipeline and league scope

## Verified Goaloo league scope

The current Goaloo adapter does not discover every league on its own. Its `get_leagues()` response is built from `ENABLED_LEAGUES` and validated against Goaloo's live season schedule response. The following values were queried through the adapter and the returned provider names matched on 2026-11-19:

| Competition | Goaloo ID | Adapter result |
| --- | ---: | --- |
| English Premier League | 36 | Verified |
| Turkey Super Lig | 30 | Verified |
| Spanish La Liga | 31 | Verified |
| German Bundesliga | 8 | Verified |
| France Ligue 1 | 11 | Verified |
| Holland Eredivisie | 16 | Verified |
| Denmark Superliga | 7 | Verified |
| Switzerland Super League | 27 | Verified |
| UEFA Champions League | 103 | Verified |
| UEFA Europa League | 113 | Verified |
| UEFA Conference League | 2187 | Verified |

These eleven competitions are the verified `ENABLED_LEAGUES` value:

```text
36,30,31,8,11,16,7,27,103,113,2187
```

The live provider's season list includes `2021-2022` through `2026-2027` for all eleven competitions. The UEFA endpoints use Goaloo's distinct `c<ID>` cup contract and `TeamList` schema; domestic competitions use `s<ID>` and `TeamInfo`. Cup round metadata rows are skipped, fixture rows are validated against the requested July-to-June season window, and only rows with the verified fixture layout are normalized. A listed season means Goaloo advertises that season; a date with no fixtures remains `EMPTY` and is never filled with generated data.

Serie A is intentionally out of scope; it can be added to `ENABLED_LEAGUES` after its Goaloo league ID and schedule contract are verified.

## Historical scope and command

The configured season scope is:

```text
2021-2022,2022-2023,2023-2024,2024-2025,2025-2026,2026-2027
```

`HISTORICAL_START_DATE=2021-07-01` caps the lower date bound. Current-season requests stop at today's UTC date. The backfill is operator-started and never runs as a web or worker startup loop.

```powershell
cd backend
python -m app.jobs.historical_backfill --dry-run
python -m app.jobs.historical_backfill --league 36 --season 2024-2025 --start-date 2024-08-01 --end-date 2024-08-31 --batch-size 7 --dry-run
python -m app.jobs.historical_backfill --league 36 --season 2024-2025 --start-date 2024-08-01 --end-date 2024-08-31 --batch-size 7
```

`--league` and `--season` accept comma-separated values, but every value must also be enabled in the environment. `--batch-size` controls the maximum date window sent to one ingest call. Re-running the same command resumes through the `HistoricalIngestDay` lease ledger and deterministic R2 object keys.

## Storage contract

Historical fixtures and verified final results are normalized and written directly to Parquet/ZSTD in R2:

```text
matches/season={season}/league={league}/date={YYYY-MM-DD}.parquet
results/season={season}/league={league}/date={YYYY-MM-DD}.parquet
odds/season={season}/league={league}/date={YYYY-MM-DD}.parquet
predictions/season={season}/league={league}/date={YYYY-MM-DD}.parquet
```

Each write is followed by object existence/size, Parquet row-count, SHA-256, and metadata verification. PostgreSQL receives only progress and archive ledger rows during a historical backfill; `postgres_rows_written` and `postgres_rows_deleted` in the data-quality report remain zero for that job. No `ActiveMatch`, `ActiveOdds`, or `MatchResult` bulk dump is performed.

The Goaloo historical odds endpoint returned opening, latest, and live rows for a finished 2024-2025 fixture in a read-only live smoke request. Historical backfill stores only `OPENING` and `LATEST` prematch values for finished fixtures; it drops live rows and never labels latest as closing. Goaloo did not return a source capture timestamp, so the archive stores `retrieved_at` and an explicit null `source_captured_at` instead of inventing one. If any R2 object or archive ledger entry already exists for that deterministic key, the backfill skips new quotes and preserves the existing archive.

Paper predictions can be copied to R2 after settlement for analysis, but cleanup never deletes prediction rows. Match cleanup is allowed only after a matching verified R2 fixture partition exists and no odds, result, prediction, or coupon item still references the match.

## Retention and storage guard

| Setting | Value |
| --- | ---: |
| `ACTIVE_STATE_RETENTION_DAYS` | 14 |
| `ODDS_RETENTION_DAYS` | 30 |
| `JOB_HISTORY_RETENTION_DAYS` | 30 |
| `POSTGRES_NORMAL_MB` | 300 |
| `POSTGRES_WARNING_MB` | 500 |
| `POSTGRES_HIGH_MB` | 750 |
| `POSTGRES_CRITICAL_MB` | 750 |

At or above 750 MB, historical ingestion returns `STORAGE_CRITICAL` before contacting the provider or writing to R2. Active match and odds collection remain separate and are not disabled by this guard. Verified R2 cleanup can continue. Paper predictions, coupons, and daily performance aggregates are retained.

## Live activation blockers

- Production Render currently reports PostgreSQL as `NOT_CONFIGURED`; the historical backfill requires a reachable PostgreSQL database for the durable lease and archive ledger.
- Production reports Goaloo as `NOT_CONFIGURED`; configure `PROVIDER_NAME=goaloo` and the verified enabled competition IDs before ingestion.
- R2 is connected, but its presence does not replace PostgreSQL's progress ledger.
- European cup schedules and historical odds remain unverified by the current Goaloo adapter.
- This change does not start a backfill, run migrations against production, alter Render settings, or deploy.
