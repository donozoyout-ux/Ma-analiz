# Production readiness and first backfill

Run operational checks inside the Maçanaliz Render service or worker process so
the command uses that resource's environment variables. Do not copy production
secrets to a workstation or repository file.

From the repository root:

```sh
cd backend
python -m app.jobs.production_readiness
```

### Protected Web Service endpoint

If Render Shell is unavailable, the same read-only gates can be run by the
Maçanaliz Web Service process:

```http
POST /api/v1/admin/production-readiness
X-Production-Ops-Token: <operator-held token>
```

Set `PRODUCTION_OPS_TOKEN` only in the Maçanaliz Web Service's Render
environment. Use a strong, unique value held by the operator; never put it in
the repository, a URL, frontend code, or logs. The endpoint returns `503` when
the environment token is not configured and `401` when the request token is
missing or incorrect. Send the header from a trusted operator environment; do
not invoke it from the public dashboard/browser.

The endpoint constructs fresh settings from process environment only
(`.env` loading is disabled), then calls the shared readiness checks. It
returns only PostgreSQL, Alembic, R2, Goaloo, and aggregate status fields. A
successful response still has `backfill_allowed: false`. It never runs a
migration or backfill. The R2 connectivity check writes a uniquely owned smoke
object, verifies it, and deletes it before returning.

Example request (the token is read from the operator's environment and is not
shown in the command):

```sh
curl --fail-with-body -X POST "https://<ma-analiz-service>/api/v1/admin/production-readiness" \
  -H "X-Production-Ops-Token: ${PRODUCTION_OPS_TOKEN}"
```

The API process must be restarted/redeployed after adding the Render variable
before the endpoint can authenticate requests.

The default command performs read-only PostgreSQL, Alembic, Goaloo and internal
readiness checks, plus a uniquely named R2 smoke object that it reads,
verifies, and deletes. It never runs a migration or backfill. Output contains
statuses, non-secret database identity, migration revisions, and the Goaloo
sample counts; it never prints connection strings or credentials.

The job rejects known legacy Betapp/Kuponanalizi database names. PostgreSQL
must report the current Alembic revision as `0008_historical_ingest_leases`,
and the code head must match. If the current revision is behind, the job reports
`NEEDS_MIGRATION` and exits non-zero; it does not upgrade the database.

Only after reviewing a readiness-only run, a separately authorized operator
may run the explicit one-day operation:

```sh
cd backend
python -m app.jobs.production_readiness --run-backfill
```

That flag is gated by PostgreSQL, Alembic, R2, and the real Goaloo league,
season, fixture, result, and odds checks. It invokes exactly one day:
Premier League (`36`), season `2021-2022`, `2021-08-13`, batch size `1`. It
then verifies the `matches`, `results`, and `odds` R2 objects and their
`ArchiveJob=VERIFIED` records. The command does not run cleanup. Do not use this
flag as a bulk historical backfill mechanism.

The command is prepared for Render but has not been deployed or run with
production credentials. Migration, smoke, and historical writes must only be
performed in Render after the code is deployed through the normal review flow.
