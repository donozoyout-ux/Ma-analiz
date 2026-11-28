# Prediction ledger foundation — incomplete

This branch contains offline domain checks only. It does not persist, publish, settle, expose an API, backfill records or add a migration. In particular, a caller-supplied timestamp is not evidence of publication, a source string is not independent provenance, and an ordinary Python object provides no append-only, concurrency or database role protection. Do not connect this module to production flows until those guarantees are designed and tested.

Remaining work: independent durable publication and settlement tables, immutable database roles and triggers, safe source proof and trusted timestamps, idempotency, corrections/supersession, PostgreSQL isolation and concurrency tests, rollback policy and paginated read-only API. Legacy active predictions must remain unverified and must not be backfilled as publications. Existing cleanup and results jobs are unchanged.
