# Source snapshot — Drive preparation (stage 1)

This workflow is a **safe first step**, not a completed Google Drive backup.

## What runs
- `.github/workflows/source-snapshot.yml` runs on `main` pushes or manually through `workflow_dispatch`.
- Uses only `contents: read` and an exact checked-out commit.
- Creates a source-only `git archive` tar.gz. It **does not include the Git history**, the local `.git` directory, runner environment or GitHub Secrets.
- Blocks archiving when tracked filenames look like local environment files, keys, credentials, databases, or Parquet datasets.
- Produces a SHA-256 checksum and a manifest with the commit ID, and verifies archive integrity.
- Uploads the result as a **GitHub Actions artifact with 7-day retention**. It does **not** upload to Drive.
- Does not run SQL, R2, Render deployment, retention, or deletion.

## Restore / verification
Download the matching workflow artifact. Extract the ZIP downloaded from GitHub Actions; it contains the tar.gz, SHA256SUMS and MANIFEST.txt. From the extracted directory:

```bash
sha256sum -c SHA256SUMS
tar -tzf Ma-analiz-source-<sha>.tar.gz | head
mkdir restored
tar -xzf Ma-analiz-source-<sha>.tar.gz -C restored
```

This restores the committed source snapshot **only**, not commits/branches/tags, production database, R2 objects, deployment secrets, or runtime volumes. Do not treat the GitHub artifact as an independent disaster recovery location.

## Stage 2: Google Drive upload — NOT ENABLED
Target folder is `MacAnaliz-Backups`. The Google Drive folder was created in the user's Drive, but ChatGPT's Google Drive connection is **not** a GitHub Actions OAuth credential and must not be copied to Actions.

Before enabling an upload:
1. Create a narrowly scoped Google automation identity with access only to the destination folder (avoid long-lived service account keys where feasible).
2. Store credentials in GitHub Actions Secrets, never in repository files or logs. Do not expose the folder publicly.
3. Add a separately reviewed upload job, initially manual, which verifies SHA256 before and after transfer, uses commit-addressed names, and fails closed.
4. Test Drive restore/readback and access permissions; only then enable automatic upload on every `main` push.
5. Add throttling/retries and backup retention strategy, and alert on failures.

**Limit:** Filename blocking is a basic safeguard, not a comprehensive secret scanner. Audit the code and history for historical credential leaks before granting external backup permissions. This archive excludes history intentionally.

**Safety:** `AUTO_RETENTION_DELETE_ENABLED` and `OPERATIONAL_DELETE_ENABLED` must stay `false`; `LIVE_DELETE=NOT_RUN`. This workflow does not reference these secrets or perform any production mutations.
