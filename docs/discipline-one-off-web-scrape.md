# One-time 2024/25–2026/27 scrape → R2 archive (strictly permission-gated)

This is the **actual manual transfer workflow**, rather than an automatically
scheduled web scraper. It uses only the **nine documented public CSV URLs**
under `https://www.football-data.co.uk/mmz4281/` with a maximum 3 MiB
per file, no redirects, no captchas/robot bypass, and a default 60-second
pause between the nine requests. A 401/403/429 fails closed; do not retry
from other IPs or use proxies to circumvent the source's restrictions.

**Rights blocker:** The source's official
[usage policy](https://www.football-data.co.uk/contact.php) explicitly limits
the freely downloadable data to personal/academic uses, and does not
authorize commercial/AI data training projects. A pending email is **not**
permission. Do not run the downloader for planned public-app/AI uses until an
appropriate contract is received, or substitute an independently licensed
source. Attribution and commercial/AI use must be covered.

No real third-party CSV was downloaded/archived in this PR, and no actual
production R2 object or SQL migration was created by this development.

## One-time download, only after rights are confirmed

On a secured operator computer or a protected one-off runner **with an
approved provider agreement**, never on the public web app:

```bash
mkdir -p /secure/local/discipline-2024-27
cd backend
python -m app.jobs.discipline_source_download \
  --output-directory /secure/local/discipline-2024-27 \
  --permission-reference YOUR_ACTUAL_APPROVED_CONTRACT_ID \
  --confirm-provider-terms \
  --confirm-one-time-download
```

The downloader writes nine CSV files and `manifest.json` with original
SHA-256 and coverage counts. There is **no upload in this command**.
A failed download produces no complete manifest; remove/quarantine partial
files only after recording the failure, and do not treat it as a complete
archive. Do not commit CSVs or include them in CI artifacts.

## R2-first upload, only after target and migration readiness

Offline, no-DB/no-R2 audit of all nine files and their hashes:

```bash
python -m app.jobs.discipline_batch_upload \
  --directory /secure/local/discipline-2024-27 \
  --permission-reference YOUR_ACTUAL_APPROVED_CONTRACT_ID
```

After checking the exact intended PostgreSQL and R2 bucket, its scoped write
credentials, access policy, a backup/restore test, matching Alembic heads
(including PR #71), and **staging** rehearsal, a separately authorized
operator may run the writing phase:

```bash
python -m app.jobs.discipline_batch_upload \
  --directory /secure/local/discipline-2024-27 \
  --permission-reference YOUR_ACTUAL_APPROVED_CONTRACT_ID \
  --apply --acknowledge-source-terms --confirm-r2-upload \
  --confirm-database-name YOUR_REVIEWED_MA_ANALIZ_DB_NAME \\
  --confirm-r2-bucket YOUR_REVIEWED_R2_BUCKET
```

`--apply` checks all nine manifest entries **before** the first R2 write.
Each file has its own R2 read-back verified Parquet receipt, then a PostgreSQL
index commit. If a later file fails, earlier verified entries persist and are
idempotently reused after manual investigation. Never report full coverage
until **all nine** receipts are verified and SQL rows indexed. Archive keys
use season and league; no bookmaker odds are imported.

**This cannot be executed from the current ChatGPT connection:** the
available Render PostgreSQL tool is read-only, and no Cloudflare R2 direct
upload connector is connected. The visible Render database name also matches
the script's legacy/review safeguard; it must not be bypassed without proving
the production DB's identity.

## Source alternative discovered (not automatically ingestible)

`open-matchday` publishes non-commercial-only 2026/27 CSVs on GitHub with
`home_corners/away_corners`, `home_yellow/away_yellow`, and
`home_red/away_red` for the selected leagues. Its
[DISCLAIMER](https://github.com/oladeeayo/open-matchday/blob/master/DISCLAIMER.md)
also carries upstream rights and commercial-reuse restrictions.
It is only **2026/27**, and its schema differs from Football-Data CSV.
Do not concatenate its values into the legacy import without an audited
separate parser and licence review.

**As of 2026-11-28:** R2 upload count from this PR = 0.


**R2 bucket safeguard:** The write command requires `--confirm-r2-bucket`
matching the configured `R2_BUCKET` setting exactly. A bucket mismatch
blocks **before** R2ObjectStore is constructed. This prevents accidentally
archiving licensed match data in a different storage bucket.
