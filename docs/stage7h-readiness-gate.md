# Stage 7H — fail-closed readiness gate (offline only)

This branch adds a **pure advisory assessor**, not a production deletion executor. The assessor reads already-produced `retention_validation` and `actions_watchdog` report dictionaries. It never opens a database, calls R2, sends a workflow dispatch, modifies environment variables or performs deletion.

`assess(validation, watchdog, now=aware_utc_datetime)` returns:

- `status=SAFE_TO_DELETE_NO` **unconditionally**. Only separate explicit human authorization and a separately reviewed operational run can ever change the production policy.
- `technical_ready`: a diagnostic only. True means that all required *provided* report fields pass the gate at that instant; it is **not an approval**.
- `blockers`: explicit, sorted codes including `OPERATOR_APPROVAL_REQUIRED`.

The assessor fails closed on incomplete scans, stale read-only reports, unknown dependencies, corrupted/uncertain archived records, incomplete archive ledgers or running jobs, unstable protected metadata, missing v2 schedule run+attempt audit linkage, unhealthy or unverified watchdog evidence and incorrect no-write/delete-disabled flags.

## Limits and next verification

This implementation has only synthetic/no-secrets tests. It is **not wired into production maintenance**; it cannot by itself certify independent SQL/R2 backup recovery or long-term schedule reliability.

Before any cleanup discussion: monitor genuine scheduled archive/auto/watchdog runs on Stage 7G `dae3d305`, collect a fresh read-only retention-validation report, separately verify R2 readback and recoverability for actual eligible records, review FK/child dependencies and exact plan digest, then obtain separate explicit user authorization. GitHub workflow green, source-code Drive snapshots, an old successful readback, or `technical_ready=true` are each insufficient.

**No production SQL/R2 writes, imports, migrations, retention dispatch, Render modifications or DELETE in this PR.**


## PostgreSQL 16 CI contract

PR-only `postgres-contract` job starts an ephemeral PostgreSQL 16 service in GitHub Actions. It uses `TEST_POSTGRES_URL` with a fixed loopback host and dedicated `stage7h_ci` database; the test fixture rejects a nonloopback/other database, `DATABASE_URL` presence, or an unverified database name. The CI job never receives production secrets. No external R2 calls; all archive objects are `InMemoryObjectStore`.

It exercises the **real PostgreSQL engine** (not SQLite) with genuine SQLAlchemy transactions and foreign-key enforcement: positive two-row cleanup with preserved archive ledger and history readback, missing R2 object blocking cleanup, rejected commit/rollback preserving SQL, and an unexpired record + disabled production gate. The test sets an explicit `enabled=True` only within the isolated CI fixture to exercise the cleanup code path; production flags remain disabled and no production cleanup is dispatched.

This is a disposable test-database write/delete and does **not** prove point-in-time recovery of the production PostgreSQL database, actual R2 disaster recovery, schedule delivery reliability, or authorize any production deletion. CI service health and run outcome must be checked before claiming PostgreSQL acceptance.
