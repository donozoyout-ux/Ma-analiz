# Maçanaliz Render deployment architecture

The frontend and FastAPI remain together in one native Python Render Web Service. A single Background Worker runs bounded scheduled ingestion, result settlement, and scanning. One daily Cron runs archive, cleanup, and metrics. PostgreSQL is external to this Blueprint. Keep Maçanaliz's database and credentials separate from Kuponanalizii.

## Render service configuration

- Repository: `donozoyout-ux/Ma-analiz`, branch `main` after this PR is merged.
- Runtime: Python native runtime. Render's native runtime includes Node.js and npm for the frontend build and server.
- Root Directory: repository root (`.`).
- Region: Frankfurt.
- Build Command: `python -m pip install -r backend/requirements.txt && cd frontend && npm ci && npm run build`
- Pre-Deploy Command: `cd backend && alembic upgrade head` (requires a paid Web Service plan).
- Start Command: `node frontend/scripts/run-single-service.mjs`
- Health Check Path: `/health/live`.
- `PORT` is supplied by Render and used for the public Next.js server. FastAPI binds only to `127.0.0.1:8000` (`BACKEND_PORT`). The two ports must differ.

The Node supervisor starts FastAPI, waits for its process-only liveness route, then starts Next.js. If either process exits unexpectedly, it terminates the other and exits unhealthy. On SIGTERM/SIGINT it signals both children and waits up to 25 seconds before force-killing any remaining child. The API is not started through shell backgrounding.

### Background Worker

- Runtime: Python, repository root.
- Build: `python -m pip install -r backend/requirements.txt`.
- Start: `cd backend && python -m app.worker`.
- Instances: one. This polling worker uses PostgreSQL `job_runs`; no queue/Redis is required for the current serialized workload.
- Defaults: fixtures hourly; odds, results/settlement, and scanner every 30 minutes; job history pruning daily. Each interval is configurable through its `WORKER_*_INTERVAL_SECONDS` environment variable.

### Daily Cron

- Runtime: Python, repository root.
- Schedule: `0 2 * * *` UTC.
- Build: `python -m pip install -r backend/requirements.txt`.
- Command: `cd backend && python -m app.jobs.daily_archive`.
- This one command processes previous-day results, archives data, cleans only verified R2 partitions, then calculates daily metrics. `daily_pipeline` is not scheduled.
- `FAILED`, `PARTIAL`, and configuration-blocked `SKIPPED` outcomes produce a non-zero process exit so Render records a failed run. Empty/no-op work remains a successful run.

## Routing and CORS

- `/` and frontend pages are served by Next.js on Render's `PORT`.
- `/api/v1/*` is rewritten by Next.js to `http://127.0.0.1:8000/api/v1/*`.
- `/health/live` is rewritten to FastAPI's dependency-free `GET /health/live`.
- Browser code continues to use same-origin `/api/v1/*` paths. `CORS_ORIGINS` is empty; the browser does not call the backend's loopback URL directly.
- The single-service exception for loopback API rewrites is enabled only with `SINGLE_SERVICE_MODE=true`. Other production builds still reject loopback API targets.

## Environment variables

The Web Service, Worker, and Cron must use the same external Maçanaliz `DATABASE_URL`. Configure provider, archive, AI, and notification variables on the services that need them:

- `DATABASE_URL`: connection URL for the separately provisioned Maçanaliz PostgreSQL. The Blueprint prompts for it; it does not create or reference a database resource.
- `PROVIDER_NAME=goaloo` and `ENABLED_LEAGUES`: configured Goaloo league IDs, supplied in Render's environment.
- `API_INTERNAL_URL=http://127.0.0.1:8000`, `SINGLE_SERVICE_MODE=true`, and `BACKEND_PORT=8000` for the internal API proxy.
- `PYTHON_VERSION=3.12.11`, `NODE_VERSION=22`, `APP_ENV=production`, and `CORS_ORIGINS` empty.

`render.yaml` carries the same Goaloo, database, R2, and notification variable names to the Worker/Cron that need them. The Worker additionally receives `WORKER_INTERVAL_SECONDS`, `WORKER_MATCH_INTERVAL_SECONDS`, `WORKER_ODDS_INTERVAL_SECONDS`, `WORKER_RESULTS_INTERVAL_SECONDS`, `WORKER_SCANNER_INTERVAL_SECONDS`, `WORKER_PRUNE_INTERVAL_SECONDS`, `JOB_RUN_STALE_AFTER_SECONDS`, `HISTORICAL_INGEST_LEASE_SECONDS`, and `JOB_HISTORY_RETENTION_DAYS`. Goaloo base URLs, timeout, retries, and request rate are set in the Blueprint. Do not add Goaloo API keys; this adapter uses public source endpoints and may be subject to source-side availability or schema changes.

Optional integrations can be added manually to the service environment:

- R2: `R2_ENDPOINT`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET`, optional `R2_REGION`.
- Groq: `AI_PROVIDER=groq`, `GROQ_API_KEY` (or the existing `AI_API_KEY` fallback), `GROQ_MODEL`, optional `GROQ_BASE_URL`.
- Telegram: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`.

Never put secret values in Git, the Blueprint, or logs. Missing optional R2/Groq settings must remain reported as not configured; they do not prevent the API process from starting.

## Migration and database

Set `DATABASE_URL` to the new Maçanaliz database before enabling deployments. The paid-service Pre-Deploy Command runs `alembic upgrade head` before the new version starts. The Start Command does not run migrations. Review migration compatibility and backup/rollback procedures before the first production migration.

The Blueprint does not create a PostgreSQL resource. Database lifecycle, backups, and credentials are managed separately.

## Local production verification

From the repository root:

```sh
python -m pip install -r backend/requirements.txt
cd frontend
npm ci
API_INTERNAL_URL=http://127.0.0.1:8000 SINGLE_SERVICE_MODE=true npm run build
npm run typecheck
npm run test:single-service
```

The integration smoke command launches both production processes with a non-default `PORT`, clears database/provider/storage/AI credentials for the child processes, verifies the frontend root and both API rewrites, then sends SIGTERM and checks that both ports stop responding. The test does not claim external service connectivity. For a deployed service, set `SERVICE_ORIGIN` and run `npm run smoke:production-api`; that checks route plumbing and reports the database status without treating a missing dependency as connected.

Goaloo/R2/Postgres/Groq live tests remain separately reported as PASS only when their actual live checks run; otherwise report `NOT_TESTED`/SKIP.

## Operational limitation

The Worker is intentionally configured for one instance. Do not scale it horizontally until job leases and duplicate behavior are designed for multiple workers. Historical backfill remains a manually scoped command; it is not run continuously or added to the daily Cron. Render Cron is a separate bounded process, not a loop inside the Web Service.
