# Prediction ledger: offline evidence boundary, not production ready

PR #71 / Issue #70. See `docs/prediction-ledger-evidence-contract.md` for the trust contract and release gates.

0011 creates immutable publications/settlements independent of expiring operational rows. 0012 adds signed delivery/result attestations and requires them for NEW settlements. No historic rows are rewritten/backfilled. Legacy settlements remain provisional and cannot enter verified metrics without new qualifying evidence.

Publication and delivery receipt clocks are database-generated, with deferred pre-kickoff commit guards. Locks/unique indexes serialize replay/revision races. settle_final() refuses temporary MatchResult: FINAL plus verified_at does not prove independent regulation-time verification. settle_evidence() uses permanent evidence even after operational match deletion. Corrections append, never overwrite.

GET /api/v1/prediction-ledger keeps date, timezone, league, market, model and status filters. Prefer next_cursor with cursor and identical filters. Keyset order is publication time/UUID descending. Offset remains compatible but may shift on concurrent inserts; cannot combine offset and cursor. This is not a snapshot across concurrent corrections. Events are bounded to 1000 per publication.

Metrics report provisional counts separately. Verified sample_size/hit_rate require latest publication + current result evidence + latest settlement + accepted pre-kickoff delivery. WON/(WON+LOST) excludes pending/void/unscored. Empty intersection yields sample_size=0, hit_rate=null. A new result correction immediately excludes the old score, even before settlement catches up. Item evidence IDs identify provenance; events preserve previous scores. No odds are inferred.

New publication/evidence entry points default disabled. No production trust policy, public dispatcher, independent observer or result-source adapter is provisioned. No production route/scheduler is wired; no environment variable enables a missing integration. Signatures in tests are synthetic only.

Local PostgreSQL **19** uses fresh loopback clusters 55471/55472. CI uses PostgreSQL **16** in two containers. Recovery checks pg_dump + globals restore, row hashes/history, role flags, trigger owner/search_path, ACLs and a real restricted test login. This does not certify production identity or production recovery.

Separate Draft [PR #72](https://github.com/donozoyout-ux/Ma-analiz/pull/72) fixes only the Bundesliga test fixture: server-default availability timestamps were after fixed NOW, correctly causing NO_AVAILABLE_REVISION. Production archive behavior is unchanged.
