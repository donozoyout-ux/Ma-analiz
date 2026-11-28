# La Liga and Premier League score-only R2 archives

**Scope**: `es.1`, `en.pl`; seasons `2024-2025`, `2025-2026`, `2026-2027` (last observed day 2026-11-28). Authoritative repository snapshot: `openfootball/football.json`, Git revision `e6744429ee395bc86f247348c6184bb08d4eb361`, CC0-1.0 project declaration: https://github.com/openfootball/football.json/blob/master/LICENSE.md.

**Important**: this is a release candidate for upload, NOT confirmation the R2 upload ran. No Render job, automatic ingestion, GitHub secrets, or production worker added. Current production `/archive` exposes only Bundesliga; frontend must be separately extended to see the additional leagues. The source files include future fixtures, filtered out from this archival snapshot. Missing scores stay unknown.

Six canonical OpenFootball files follow `{revision}/{year-short}/{es.1|en.1}.json`. The uploader expects exactly 380 fixture records per source file, checks scope, team identity and score shapes, excludes games after cutoff, then encodes immutable Parquet with SHA-256 readback and content-addressed receipt/manifests. Each league-season writes at most three new objects under `season-bulk/openfootball/season=YYYY-YYYY/league=es.1|en.pl/`; Bundesliga prefix, existing score and daily archives are untouched. No PostgreSQL, odds, corners, cards, shots, or The Odds API.

Default `python -m app.jobs.es_en_season_r2`: downloads six pinned public files to memory and checks counts/checksums and staging objects, **zero R2 requests**. Tests use synthetically generated files and in-memory store. Real live R2 upload is a **separate privileged operator operation**, requires correct bucket `sdfgdsgdg`, read/write credentials in the execution environment and the exact explicit opt-in:

```shell
python -m app.jobs.es_en_season_r2 --live --approval ARCHIVE_ES_EN_2024_2027_R2_ONLY
```

The job prepares all six sources before opening R2, HEAD/GET-verifies existing objects and aborts on conflicting keys; put-if-absent and SHA-256 readback prevent overwrites. Successful execution must return `R2_ARCHIVE_VERIFIED` with 18 verified objects. Running it twice must reuse the exact objects. Verify the resulting R2 objects externally before claiming upload.

**Restrictions:** Do not run the live mode from untrusted PR CI; do not expose secrets or collect statistics/odds. A dedicated authorized executor with approved R2 credentials is required. A deployed archive API/viewer for the new leagues is a separate subsequent change, not part of this uploader.
