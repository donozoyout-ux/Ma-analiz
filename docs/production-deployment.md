# Maçanaliz production deployment notes

This document describes the current Render configuration for this repository. It does not contain credentials. Keep Maçanaliz resources separate from Kuponanalizii: use the `mac-analiz-*` service names and a newly created Maçanaliz PostgreSQL database. Never point `DATABASE_URL` at the other application's database.

## Render services

### Backend Web Service

- Runtime: native Python; root directory: `backend`; region: `frankfurt`.
- Build: `pip install -r requirements.txt`.
- Pre-deploy: `alembic upgrade head` (requires a paid Render web service).
- Start: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`.
- Health check: `/health/live`. The route checks only the API process.
- `render.yaml` also describes the worker, database, and scheduled jobs. Review the planned resource list before applying any Blueprint so it is clear which services will be created.

### Frontend Web Service

- Runtime: Node.js 22; root directory: `frontend`; region: `frankfurt`.
- Build: `npm ci && npm run build`.
- Start: `npm start`.
- Set `API_INTERNAL_URL` from the Backend Web Service's Render `hostport` property. Render makes the referenced environment variable available to the frontend build and running service; Next.js captures the rewrite target while building.
- Production builds fail if `API_INTERNAL_URL` is absent or points to loopback. The development fallback to `http://localhost:8000` is local-only.
- The browser uses same-origin `/api/v1/*` requests and Next.js rewrites them to the backend. Therefore `CORS_ORIGINS` is empty in the Blueprint. If the frontend is later changed to call the backend directly from a browser, set `CORS_ORIGINS` to the exact HTTPS frontend origin.

## Environment variables

Set variables in Render's service environment or by service references. Do not commit secret values or include them in logs.

### Backend

- Required for persistent state: `DATABASE_URL`, referenced from the new Maçanaliz Render PostgreSQL `connectionString`.
- Goaloo: `PROVIDER_NAME=goaloo`, `GOALOO_BASE_URL`, `GOALOO_ODDS_BASE_URL`, `ENABLED_LEAGUES` (comma-separated configured Goaloo league IDs), optional `ENABLED_SEASONS`, `PROVIDER_REQUEST_TIMEOUT_SECONDS`, `PROVIDER_REQUESTS_PER_MINUTE`, `PROVIDER_MAX_RETRIES`.
- Optional R2 historical archive: `R2_ENDPOINT`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET`, optional `R2_REGION`.
- Optional Groq: `AI_PROVIDER=groq`, `GROQ_MODEL`, and `GROQ_API_KEY`. `AI_API_KEY` remains a supported fallback for the key. `GROQ_BASE_URL` defaults to the official OpenAI-compatible endpoint.
- Optional notifications: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`.
- Other runtime settings include `APP_ENV`, `LOG_LEVEL`, and `CORS_ORIGINS`.

`ENABLED_LEAGUES` is intentionally supplied by the deploy operator rather than populated with guessed IDs. Without it ingestion safely reports that provider coverage is not configured. Groq and R2 are optional to API startup; their dependent features report unavailable/not-configured and must not be represented as connected.

### Frontend

- Required: `API_INTERNAL_URL` (Render service reference to the Backend Web Service's `hostport`).
- `NODE_VERSION=22`.

## Migration procedure

The API start command does not run migrations. On paid Render, `preDeployCommand` runs `alembic upgrade head` before a new backend version starts. Confirm the database reference is the new Maçanaliz database and review Alembic revisions before the first production run. For initial setup, provision the database, run and verify the migration, then enable worker and scheduled ingestion. Use backward-compatible migrations when services can deploy concurrently; pause ingestion for schema changes that are not backward compatible. No production migration has been run as part of this preparation.

## Health and API verification

- Render liveness: `GET /health/live`, with no database, R2, provider, or AI calls.
- Diagnostics/readiness: `GET /api/v1/health`; the payload reports `status=degraded` when PostgreSQL is unavailable or not configured.
- After both services are live, run `npm run smoke:production-api` in `frontend/` with `FRONTEND_ORIGIN` and `BACKEND_ORIGIN` set to their deployed URLs. The script checks backend liveness and that the frontend rewrite reaches a backend reporting a connected database. It does not create data or use credentials.

## Goaloo data and odds caveats

`backend/app/providers/goaloo.py` is the Goaloo adapter. It validates source response shapes and fails on unsupported status/schema values instead of fabricating fixtures. It maps 1X2, Asian Handicap, and Over/Under quotes. `OPENING` and `LATEST` are distinct prematch snapshots; live quotes use `LIVE` period/type. The adapter does not infer a `CLOSING` quote from the latest value. Closing-line metrics remain unavailable until Goaloo exposes a verified closing snapshot contract.

The fixture-based adapter tests do not count as a live provider check. `backend/tests/test_goaloo_live.py` performs an opt-in request using the configured Goaloo leagues and reports `NOT_TESTED` as a pytest skip if configuration, live fixtures, or provider availability is missing. A live response schema change remains a test failure requiring review.
