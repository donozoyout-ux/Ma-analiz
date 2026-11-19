# Production readiness and first backfill

Run operational checks inside the Maçanaliz Render service or worker process so
the command uses that resource's environment variables. Do not copy production
secrets to a workstation or repository file.

From the repository root:

```sh
cd backend
python -m app.jobs.production_readiness
```

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
