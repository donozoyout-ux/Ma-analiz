# Maç Analiz

Sports analytics and paper simulation platform. It does not place bets or move real money.

## Repository and scope

This implementation is in the active checkout Maçanaliz (Latin a). An earlier setup also created a similarly named Maçанализ copy outside this repository (Cyrillic а); it was not used as the working repository and its files were not cleaned up automatically.

The upstream repository initially contained only a README. This repository now includes a FastAPI backend, PostgreSQL active-state schema and Alembic migration, R2/Parquet storage adapter, provider abstraction, bounded worker jobs, Next.js dashboard, Docker Compose, and Render Blueprint.

## Architecture and safety

- PostgreSQL contains current operational state and compact daily aggregates. Historical Parquet data belongs in R2; no historical SQL repository is provided.
- R2 has an explicit S3-compatible adapter and a separate in-memory implementation for tests. Missing credentials report R2_NOT_CONFIGURED; storage sizes remain unknown rather than fabricated.
- Archive keys are deterministic. Uploads are checked by object size, row count, SHA-256, and persisted state transitions. Database deletion requires a persisted VERIFIED archive, a checksum/row count, row locking, and a one-day buffer.
- Provider normalization isolates upstream response shapes. No live provider credentials or production ingestion adapter are included. The empty provider reports MOCK/MOCK_PROVIDER_EMPTY explicitly.
- The worker and three daily cron entrypoints share idempotent job records. Unsupported or unavailable sources produce SKIPPED; they do not claim ingestion, cleanup, archive, or metrics succeeded. PostgreSQL job history is pruned after the configured JOB_HISTORY_RETENTION_DAYS (default 30).
- Dashboard cards and charts read API data. Missing daily metrics display No data yet; no sample production values are included.
- Telegram sending and automated strategy changes remain gated abstractions; this phase does not enable wagering or real-money actions.

## Remaining before live V1 ingestion

- Implement and configure a real sports data provider, then persist normalized current matches and odds.
- Connect daily archive and cleanup handlers to real database partitions. Until that extraction/deletion adapter exists, cron jobs report a reasoned SKIPPED state and do not delete records.
- Implement result finalization and daily aggregate calculation from verified results. The metrics cron stays skipped without that source.
- Validate migrations against a live PostgreSQL instance, verify real R2 credentials and operations, and perform a live Render deployment only when configured.

## Local development

Copy .env.example to .env, start Docker Desktop, then run docker compose up --build. The Compose file defines PostgreSQL, API, worker, and frontend. The worker records skipped jobs until a real provider is configured. For API-only development, from backend, create a virtual environment, install requirements.txt, then run uvicorn app.main:app --reload.

## Verification

- Backend: py -m pytest -q
- Frontend: npm run typecheck and npm run build
- Compose syntax/config: docker compose config -q
- Render Blueprint: validated against Render's published JSON Schema; no deploy performed.
