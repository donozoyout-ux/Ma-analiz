# Stage 7H.3 — Independent object backup/recovery (synthetic only)

This phase tests the **R2 disaster-recovery concept** using two independent `InMemoryObjectStore` instances and the archive ledger from the temporary SQLite fixture. There is **no Cloudflare R2 connection**, external bucket, production SQL connection, export of real data, or production cleanup.

## Acceptance checks
1. Archive two synthetic MATCH/RESULT objects and verify SHA-256, byte size, decoded Parquet row count/content and archive-key metadata against each `archive_jobs` ledger row.
2. Copy verified objects into a physically distinct in-memory replica, independently read them back against the same SQL ledger, and preserve source immutability.
3. Within the disposable SQLite test only, invoke the real gated SQL cleanup implementation, confirm removal of synthetic operational rows, then simulate total loss of the primary in-memory object store.
4. Recover all objects into a *new empty* target using only the replica. Verify full object equality, retain ArchiveJob ledger, and recover Match Center detail and score from restored history.
5. Fail closed for missing, corrupted, metadata-tampered or extra replica object; destination must remain empty on preflight failures. Reject source corruption, unverified ledger, altered ledger checksum and overwrite of nonempty destination.

`r2_recovery_support.py` lives exclusively under `backend/tests` and cannot be imported from application runtime without going outside the package. It is **not a production R2 backup tool**. A verified in-memory replica is not an offsite/independent failure-domain backup: external replication, access controls, object versioning, retention, encryption, restore time and full-scale inventory remain unproven.

## Production gates
**SAFE_TO_DELETE=NO** regardless of test results. Complete independent real-R2 read-only verification, PostgreSQL PITR/backup policy, monitoring of Stage 7G natural archive/auto runs, and separate operator approval before considering production delete. Never enable `AUTO_RETENTION_DELETE_ENABLED` or `OPERATIONAL_DELETE_ENABLED` from these tests.

No real R2 PUT/DELETE, SQL DELETE, import, migration, Render configuration or scheduler dispatch. PR stays draft until review.
