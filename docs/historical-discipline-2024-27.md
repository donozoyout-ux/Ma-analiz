# MA-ANALİZ — 2024/25 onward corner/yellow/red card archive

## Status: Draft / prepared, NOT populated in production

Scope: English Premier League, German Bundesliga and Spanish La Liga for
2024/25, 2025/26 and 2026/27 (the last season only up to available finished
matches). Exactly **nine manually downloaded season CSV files**.

| Competition | Code | 2024/25 | 2025/26 | 2026/27 |
| --- | --- | --- | --- | --- |
| Premier League | E0 / en.pl | `2425/E0.csv` | `2526/E0.csv` | `2627/E0.csv` |
| Bundesliga | D1 / de.bl1 | `2425/D1.csv` | `2526/D1.csv` | `2627/D1.csv` |
| La Liga | SP1 / es.ll | `2425/SP1.csv` | `2526/SP1.csv` | `2627/SP1.csv` |

Source URL format: `https://www.football-data.co.uk/mmz4281/{year}/{code}.csv`.
Official catalogue: https://www.football-data.co.uk/data.php.
Official usage policy: https://www.football-data.co.uk/contact.php.

### License blocker before public or commercial usage

Football-Data explicitly describes the *free* CSV usage as mainly for
**private individual and academic research**; automated access should be
light (no more than twice a week). Its free data are **not intended for
commercial or AI training projects**. Upstream stats also have separate
provider rights. Public deployment, redistribution, model training or
commercial use needs **written permission or a clearly compatible data
license**. Do not assume "free download" means "free redistribution".

This repository contains **no actual provider CSV rows** and never downloads
them in CI, at startup or with a scheduler. Data rights must be reviewed and
recorded by the operator before any import, public display or model training.
User-supplied permission text is a manual trace, NOT automatic legal proof.

### Data contract

Only these columns are imported:

- `Div`, `Date`, `HomeTeam`, `AwayTeam` for source identity,
- `HC` and `AC`: home and away corners,
- `HY` and `AY`: home and away yellow cards,
- `HR` and `AR`: home and away red cards.

All other columns, including **all betting odds and bookmaker data**, are
discarded. Blank counts remain `NULL`, actual zero is kept as `0`.
Match averages use only records where **both** teams have that statistic.
Sample counts differ by metric. Source scores are not imported and no row
is called an independently verified `FINAL` result. Some leagues/providers
differ in treatment of second yellow cards: do not silently merge those
counting conventions across competitions.

Each record receives a deterministic SHA-256 identity from the source,
league, season, date and source team names. The table is separate from
`active_matches` and its short retention policy. Overlapping team aliases,
source identities, and date-only timestamps cannot be assumed to be the
same canonical match. Changes to an existing source key are **rejected**
rather than silently overwritten. The archive table has an UPDATE/DELETE/
TRUNCATE trigger; administrators can still alter database triggers, so
operational role review remains necessary.

`ingested_at` is set at actual SQL insertion. This is the earliest
**documented availability** of the record in MA-ANALİZ, not proof that the
publisher made the field available at a particular historical time. A
backtest as-of earlier than `ingested_at` must not use the row; the
`filtered_query(as_of=...)` guard enforces that constraint.

### Safe operator workflow (only after provider permission)

1. Obtain each official CSV manually from its source under approved usage
   conditions. Never commit, redistribute, or upload raw files into this repo.
2. Run a **local, no-database dry run**:

```bash
cd backend
python -m app.jobs.historical_discipline_import --file /local/E0.csv --league E0 --season 2425
```

The report shows file SHA-256, coverage for corners/yellows/reds, row counts
and zero bookmaker odds. Repeat for the nine combinations, adjusting league,
season and filename. It does **not** expose full underlying match rows.

3. An authorized operator must verify (a) provider rights for the use case,
(b) the database is the intended MA-ANALİZ database, (c) a backup/restore
rehearsal, (d) Alembic head, and (e) source file hash. Only then does a
manual SQL import become eligible:

```bash
python -m app.jobs.historical_discipline_import --file /local/E0.csv --league E0 --season 2425 \
  --write --acknowledge-source-terms --expected-sha256 ACTUAL_SHA256 \
  --permission-reference APPROVED_LICENSE_OR_CONTRACT_REFERENCE
```

This is **not** permission to run the command now. There is no automatic
ingest flag, cron, R2 upload or role grant in this PR. No production
connection secrets are needed for offline validation.

### Data API and dashboard

- `GET /api/v1/historical-discipline/summary?league=en.pl&season=2024-2025`
- `GET /api/v1/historical-discipline/matches?league=de.bl1&season=2025-2026&limit=20`

The `/analysis?tab=discipline` interface provides three-league season
filters, team filter, numeric sample counts and match rows. It renders
empty/503 states when no authorized records have been inserted.
The API reads SQL in read-only transactions. It returns **source-reported
metrics**, not independently verified match results, prediction success,
licensed odds or certified publication events.

### Remaining launch gates

- Obtain rights for public display and model analytics/training, or replace
  football-data.co.uk with a provider whose contract explicitly allows it.
- Confirm nine actual source files, sample hashes, field completeness,
  date/team mapping and statistical semantics. Do not fabricate coverage.
- Determine the correct Render PostgreSQL instance. The available connector
  can inspect PostgreSQL read-only but cannot execute the required production
  import; deployment/write procedures must be separately authorized.
- Execute migrations only after backup and isolated restore test. This PR
  adds `0011_historical_discipline` branching from `0010_active_statistics`;
  Draft PR #71 also introduces an independent `0011_prediction_ledger`
  from the same base. **Before merging both, create a deliberate Alembic
  merge revision** and re-run migrations from the intended production head.
- Add end-to-end UI browser tests against authorized staging data, and
  confirm no historical feature look-ahead leakage before enabling any
  predictive model dependency.
- Never enable bulk import, predictions, publication settlement, or
  automatic collection without a separately approved runbook.
