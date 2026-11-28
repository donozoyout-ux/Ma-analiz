# Cornerflick open-data read-only pilot (Bundesliga)

**Status: Draft, NOT integrated into the running application. No production traffic or data writes.**

Official data directory: https://cornerflick.com/data

Cornerflick publicly documents these downloads:

- `https://cornerflick.com/football/leagues/bundesliga/results/season.json`
- `https://cornerflick.com/football/bundesliga/matches/{match-slug}/data.json`
- Per-match `events.csv`, `lineups.csv`, `stats.csv` (not included in this pilot).

The compilation is described as **CC BY 4.0**. Attribution is
`Data by Cornerflick — https://cornerflick.com`.
Cornerflick also states that underlying feed facts come from third-party providers
with their own terms; bulk redistribution or commercial use needs separate
permission review. The site mentions API-Football on match pages. **Do not
assume the compilation's licence grants rights to bulk redistribute all underlying
match, events, lineups or stats data.**

## What this pilot implements

`app.providers.cornerflick_open_data.CornerflickOpenData` is a minimal
**opt-in, manual, read-only JSON fetcher**, not an ingestion provider.

- **Disabled by default**; requires explicit `enabled=True` in a permitted probe.
- **Bundesliga only**, fixed HTTPS host and known documented paths; no arbitrary
  URLs, no redirects, no bearer tokens, no HTML scraping.
- At most one request per instance every 60 seconds (later requests fail with a
  cooldown instead of sleeping); one attempt, no automatic retries.
- Bounded response (2 MiB), strict JSON response handling, network error
  taxonomy, transport SHA-256 and attribution.
- Returns **raw, unvalidated JSON**, intentionally without guessing field names,
  normalizing teams/scores, generating odds, or marking outcomes `FINAL`.
- Never registered in the generic provider factory or scheduler. No new API,
  SQL, R2, migration, cron, AI, prediction settlement or frontend wiring.
- Offline tests use `httpx.MockTransport`; no request to Cornerflick.

## Why live ingestion remains blocked

The direct JSON endpoints could not be retrieved from the implementation
environment, so their actual schema, field provenance, content-type, refresh
semantics, rate limits and access behaviour were **not validated**. Search-indexed
Cornerflick pages document the files but do not substitute for decoding a real
download. A SHA-256 of retrieved bytes is an integrity fingerprint **not**
independent evidence that a score or statistic is correct.

Before broad usage, obtain/review the website terms, data licence scope,
third-party data provider rights, robots/access policies, and any official
rate-limit guidance. Contact Cornerflick if redistributing or storing bulk
records. Do not bypass 401/403/429 or anti-automation controls.

## Next steps for Codex (requires a real JSON sample or permitted read)

1. Inspect a documented finished Bundesliga `data.json` and `season.json`
   using a permitted single read. Record content type, schema, provenance,
   licensing and `last_updated_at`; do not infer absent fields.
2. Validate actual 90-minute result semantics. Do not treat data alone as
   independent publication or score settlement evidence.
3. Add a separately reviewed typed adapter and source-identity reconciliation
   with OpenLigaDB; include conflict detection and correction history. Existing
   canonical IDs must not be overwritten.
4. Preserve attribution and provider rights. Never merge into SQL/R2 or update
   the verified historical archive until authorized and independently tested.
5. If approved, add a feature flag defaulting to **off** with bounded explicit
   diagnostics. Do not enable on Render, start automation or deploy in this PR.

This draft is a standalone starting point and makes **no claim that Cornerflick
is already connected to live MA-ANALİZ**.
