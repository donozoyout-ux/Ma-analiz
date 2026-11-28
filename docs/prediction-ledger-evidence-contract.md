# Publication and regulation-time evidence contract v1

Disabled integration design plus isolated-tested verifier. No claim of real
prediction delivery or real score verification is made by synthetic tests.

## Publication boundary

Today GET /predictions reads expiring rows. TelegramService.send_prediction
returns a Boolean; SQLNotificationLedger.mark_sent mutates a status after HTTP
success. None is an independent audience-access receipt or permanent publication.

Proposed approved production sequence:

1. Authorized non-owner publisher calls prepare_publication_for_delivery, disabled
   by default. DB locks source, assigns UTC time and commits before kickoff.
   The committed row is an intent, still PUBLICATION_DELIVERY_UNVERIFIED.
2. After commit a separate dispatcher reads the exact immutable prediction and
   exposes its canonical payload on an approved public channel. UUID is the
   transport deduplication key. Every attempt enforces the kickoff deadline. If
   outcome of a send is uncertain, query transport by key; never blindly resend.
   Logs, DB insertion, request start or sender SENT flag do not count as proof.
3. A separately operated observer retrieves the payload via the audience's actual
   access path, verifies its hash and signs an observation with an Ed25519 key
   outside the app. Approved audience/channel is bound in the signature.
4. A separate verifier process uses reviewed pinned public keys and the
   ma_ledger_verifier DB identity. accept_evidence verifies signature and bound
   fields before append. Writer cannot self-attest; verifier cannot publish/settle.
5. Receipt observation >= DB publication time; arrival and deferred commit both
   precede kickoff. Late receipt claiming an earlier delivery is rejected. Failure
   or uncertain delivery stays UNVERIFIED, with no timestamp backdating.

No dispatcher, public payload route or production trust store is installed here.
Existing GET/Telegram paths are unchanged. Independent observer selection, channel
access policy, transport deduplication, key issuance/rotation/revocation and signed
artifact retention remain release gates. Setting an environment flag cannot
activate a missing integration. New internal boundaries default enabled=False.

## Signed envelope and trust boundary

Canonical UTF-8 JSON uses sorted keys, compact separators, ASCII escapes and no
NaN. Domain-separated schema ma-ledger-evidence/v1 binds kind DELIVERY/RESULT,
authority, key_id and event_id. Only the verifier's reviewed injected TrustPolicy
selects public keys; untrusted payloads cannot install keys or permissions.

Every envelope binds match UUID, provider/native ID, league, season, home/away
identity and UTC kickoff. Delivery binds publication UUID and SHA256 of the whole
immutable prediction (market, selection, probability, model/feature hash, revision
and DB publication time), audience and PUBLICLY_RETRIEVED observation. A new
publication revision needs a new receipt.

Rows retain payload, canonical SHA256, signature, authority, key ID/public-key
fingerprint, observation time, DB receipt time and predecessor UUID. Unique
authority/event keys reject altered replay; identical replay reuses the row.
Signing/private keys exist only in synthetic test fixtures. Verification follows
the [cryptography Ed25519 API](https://cryptography.io/en/latest/hazmat/primitives/asymmetric/ed25519/).

A signature authenticates an approved authority's assertion, not sporting truth
or that authority's independence. SQL triggers do not verify Ed25519; direct SQL
by the verifier role is a trusted boundary. Separate its process/credentials from
the app/publisher. A privileged DBA or compromised verifier can bypass semantics.
Revoking previously accepted receipts/keys is not implemented and is an explicit
production release blocker. Never accept TrustPolicy from request parameters.

## Regulation-time result and retention

Only REGULATION_90_PLUS_STOPPAGE qualifies. Extra-time/penalty totals are not
inferred/subtracted/reused. FINAL requires explicit nonnegative integer scores.
CANCELLED/RETRACTED require null scores and yield VOID/UNSCORED. Postponed or
unverifiable sources remain pending; a prior result requires signed RETRACTED
evidence to withdraw it.

Each attestation includes at least two policy-approved source entries from
distinct independent organizations agreeing on status, period and scores. Each
binds native match ID, immutable artifact reference, SHA256 and permission_ref
(reviewed licensing/storage/attribution/reuse decision). The independent result
authority must validate actual bytes, mapping and permissions before signing.
This pure envelope module cannot establish those facts from URLs or strings.
Two mirrors of one source are not independent. No real authority, licensed-source
policy or historical R2 adapter is configured.

Observed time is after kickoff, never future. Evidence binds retained publication
identity and has no FK to ActiveMatch/MatchResult. First late verification and
corrections work after retention deletes operational rows. Serialized predecessor
chains reject forks/stale corrections. Evidence is publication-specific; an
adapter must explicitly fan out signed attestations for multiple publications,
never attach one to a different UUID silently.

Settlement recomputes outcome in SQL against current evidence and appends a new
revision. A new result attestation immediately invalidates the old aggregate
until consumed. Previous WON/LOST events remain auditable. Absent/late delivery
still excludes the result from verified sample size and accuracy.

## Roles, migration and restore

0011/0012 require cluster-wide CREATEROLE with fixed NOLOGIN names. Existing names
and assigned-role downgrade fail closed. DBA must audit names across databases.
Runtime connections must not own tables or be superuser/CREATEROLE/CREATEDB/
BYPASSRLS or hold schema CREATE. New boundaries check effective role. Migration
owner remains separate; no routine login is assigned automatically.

SECURITY DEFINER functions qualify tables, pin pg_catalog,public,pg_temp and
revoke PUBLIC EXECUTE. 0012 refuses PUBLIC CREATE on public schema instead of
silently changing unrelated grants. DBA must also audit named role grants,
inherited SET ROLE privileges and function/extension ownership. Administrators
can alter triggers; append-only is not protection from the DBA.

Nonempty downgrade locks tables then refuses. Use forward repair or separately
approved backup/recovery, never automatic deletion. Empty upgrade/downgrade/
re-upgrade and two-cluster synthetic dump/restore are exercised. Recovery compares
history/row hashes, roles, ACLs, trigger owner/search_path and real restricted
login behavior. Role SQL is privileged code and must come from reviewed backups.
Local PG19 and CI PG16 are separate tests, not a cross-major downgrade claim.

production_readiness.EXPECTED_ALEMBIC_HEAD changes 0011 to 0012. Deploying code
against 0010/0011 deliberately blocks that readiness gate; do not bypass it or
auto-migrate. Future separately approved order: production backup + independent
restore; DBA audit; maintenance/manual migration; schema/login compatibility
validation; deployment with delivery disabled; approved staging of real observer,
result adapters and revocation before publication enablement. None is run here.
