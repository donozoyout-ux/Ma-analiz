# Maçanaliz single-service Render deployment

This configuration runs Next.js and FastAPI in one native Python Render Web Service. It does not provision a database, worker, cron job, or separate frontend service. Keep Maçanaliz's external PostgreSQL and any storage credentials separate from Kuponanalizii.

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

The Node supervisor starts FastAPI, waits for its process-only liveness route, then starts Next.js. If either process exits unexpectedly, it terminates the other and exits unhealthy. On SIGTERM/SIGINT it signals both children and waits up to 10 seconds before force-killing any remaining child. The API is not started through shell backgrounding.

## Routing and CORS

- `/` and frontend pages are served by Next.js on Render's `PORT`.
- `/api/v1/*` is rewritten by Next.js to `http://127.0.0.1:8000/api/v1/*`.
- `/health/live` is rewritten to FastAPI's dependency-free `GET /health/live`.
- Browser code continues to use same-origin `/api/v1/*` paths. `CORS_ORIGINS` is empty; the browser does not call the backend's loopback URL directly.
- The single-service exception for loopback API rewrites is enabled only with `SINGLE_SERVICE_MODE=true`. Other production builds still reject loopback API targets.

## Environment variables

Required in the one Web Service:

- `DATABASE_URL`: connection URL for the separately provisioned Maçanaliz PostgreSQL. The Blueprint prompts for it; it does not create or reference a database resource.
- `PROVIDER_NAME=goaloo` and `ENABLED_LEAGUES`: configured Goaloo league IDs, supplied in Render's environment.
- `API_INTERNAL_URL=http://127.0.0.1:8000`, `SINGLE_SERVICE_MODE=true`, and `BACKEND_PORT=8000` for the internal API proxy.
- `PYTHON_VERSION=3.12.11`, `NODE_VERSION=22`, `APP_ENV=production`, and `CORS_ORIGINS` empty.

The Goaloo base URLs, timeout, retries, and request rate are set to the adapter defaults in `render.yaml`. Do not add Goaloo API keys; this adapter uses the public source endpoints and may be subject to source-side availability or schema changes.

Optional integrations can be added manually to the service environment:

- R2: `R2_ENDPOINT`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET`, optional `R2_REGION`.
- Groq: `AI_PROVIDER=groq`, `GROQ_API_KEY` (or the existing `AI_API_KEY` fallback), `GROQ_MODEL`, optional `GROQ_BASE_URL`.
- Telegram: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`.

Never put secret values in Git, the Blueprint, or logs. Missing optional R2/Groq settings must remain reported as not configured; they do not prevent the API process from starting.

## Migration and database

Set `DATABASE_URL` to the new Maçanaliz database before enabling deployments. The paid-service Pre-Deploy Command runs `alembic upgrade head` before the new version starts. The Start Command does not run migrations. Review migration compatibility and backup/rollback procedures before the first production migration.

The service still requires a Postgres instance externally. Since this Blueprint intentionally creates only one Web Service, database lifecycle, backups, and credentials are managed separately.

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

This one-service Blueprint intentionally omits the background worker and scheduled archive, cleanup, and metrics jobs. The API and frontend can run, but ingestion and scheduled maintenance will not run automatically in this deployment shape. If those jobs are required, they need an explicitly approved single-service process design or separate services; this Blueprint does not create them.
