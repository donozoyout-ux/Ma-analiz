# Corner / Card R2 archival plan — 2024/25 to 2026/27

**Implementation status (28 November 2026): Draft PR #74.** No real source CSV files have been imported or uploaded. A green synthetic test does not represent live data coverage.

## Data scope

Exactly three leagues and three seasons: Premier League (`en.pl/E0`), Bundesliga (`de.bl1/D1`) and La Liga (`es.ll/SP1`); 2024/25, 2025/26, 2026/27 (last season in progress).

Input source currently supported by the parser: Football-Data.co.uk per-season CSV, manually downloaded and **only after approved licence / contract for intended use**. The source's [fair-use policy](https://www.football-data.co.uk/contact.php) expressly restricts the free files to personal/academic work and excludes commercial/AI data training. Do **not** archive the free feed for public analytics/AI use absent rights. If rights cannot be obtained, use an explicitly licensed replacement source and separate reviewed parser.

Only `HC/AC` (corners), `HY/AY` (yellow cards) and `HR/AR` (red cards) plus match date, source team/league/season names are extracted. No betting odds or provider match result status is imported. Missing `null` is never treated as zero.

## R2 FIRST → verify → PostgreSQL index

1. Parse and validate a **local** league-season CSV; compute the original file SHA-256 and count complete corner, yellow and red card pairs. Dry-run makes **no** network or storage requests.
2. After explicit approval and a database backup/restore rehearsal, create one deterministic **immutable Parquet** object per source CSV under:

   `match-statistics/season=2024-2025/league=en.pl/date=2024-07-01/part=source-<full-source-file-sha256>.parquet`

   The `date=2024-07-01` segment is a *logical season anchor*, NOT a match date or observation time. Each Parquet row retains its own `match_date`.
3. Use the existing `archive_partition` with conditional PUT (`If-None-Match: *`), HEAD/GET readback, exact size, SHA-256, Arrow row count, key metadata and decoded row equality. An existing differing key is a **conflict** and is never overwritten or deleted.
4. Use `SQLArchiveTracker` to record `ArchiveJob(dataset_type='match-statistics', status='VERIFIED', checksum, size, row_count, verified_at)`. A failure remains `FAILED`/not visible.
5. **Only after** the exact R2 receipt is verified, insert `HistoricalDisciplineMatch` SQL statistic rows with `r2_archive_key`, `r2_archive_checksum`, source URL, input file SHA-256 and permission reference. Identical import is idempotent. Changed source stats for the same identity fail closed, requiring a separate reviewed correction; no silent rewrite.
6. The public read-only API joins the SQL statistic rows with a still-`VERIFIED` `ArchiveJob` matching R2 key and checksum. The dashboard displays separate source row count and **R2 ledger archive count**. This verifies **storage integrity**, not the statistical accuracy of the original source or 90-minute match result.

R2 is the archived **source-observation dataset**; SQL is the query/ledger index. A failed SQL insert may leave a verified, not-yet-indexed R2 object; that is non-destructive and can be reconciled manually. R2 objects are not automatically deleted, listed globally or overwritten. The operational source is not deleted.

## Operator commands — NOT YET AUTHORIZED FOR PRODUCTION

Dry run (no database or R2 credentials required):

```bash
cd backend
python -m app.jobs.historical_discipline_import --file /licensed/E0.csv --league E0 --season 2425
```

Only after a suitable contract/license reference, source file ownership, matching SHA-256, backup/restore and correct application SQL/R2 target have been independently verified, an authorized operator may deliberately execute:

```bash
python -m app.jobs.historical_discipline_import \
  --file /licensed/E0.csv --league E0 --season 2425 \
  --write --confirm-r2-upload --acknowledge-source-terms \
  --expected-sha256 REAL_SHA256 \
  --permission-reference REVIEWED_CONTRACT_ID_OR_URL
```

The CLI is **off by default** and has no automatic downloader, cron, GitHub Actions with secrets, or production deployment. Nine separately reviewed inputs are required to fill all selected league/seasons. The `--permission-reference` flag records an **operator assertion**; passing a made-up string does not grant a license or make data legal to redistribute.

For a cautious staging-first rollout, check R2 IAM policy scope, bucket storage/billing, read/write access, `If-None-Match` behavior, SQL `ArchiveJob` status, database ownership and backups. Perform an isolated production-like rehearsal first. No new application secrets are stored in GitHub.

## Analysis rules

Existing `/api/v1/historical-discipline/summary` and `/matches` are read-only and only expose rows with `VERIFIED_R2_LEDGER`; 7/30/90 match trends can be added after real licensed coverage is established. Show each statistic's complete-pair sample count. Historical source files imported today must not be used as features in earlier-dated predictions without actual historical availability proof; `ingested_at` provides an earliest in-platform availability bound.

**Migration alert:** PR #74 `0011_historical_discipline` and Draft PR #71 `0011_prediction_ledger` independently branch from `0010_active_statistics`. They must be reconciled using a reviewed Alembic merge revision and full isolated upgrade/downgrade/recovery tests before either is merged or deployed.

**Explicitly not done:** No production data upload, no live R2 request, no SQL migration, no model training, no prediction settlement, no merge or deploy in this PR.
