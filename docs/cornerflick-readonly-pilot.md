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

## 28 Nov 2026 follow-up: offline JSON schema inventory

The official [Cornerflick data page](https://cornerflick.com/data)
still describes free JSON/CSV exports. The finished-match page
[`bayern-munchen-vs-1-fc-koln-bundesliga-2026-11-20`](https://cornerflick.com/football/bundesliga/matches/bayern-munchen-vs-1-fc-koln-bundesliga-2026-11-20)
shows provider attribution to **API-Football**. That is a page claim,
not a separately verified licence or raw JSON schema. Direct file
retrieval from the development environment remains unavailable.

New **local-only** inspection utility:

```bash
cd backend
python -m app.providers.cornerflick_schema_inspect --season /path/to/season.json --match /path/to/data.json
```

The tool does **not** fetch any URL. The operator must obtain the files
through the site's documented download links, in compliance with the
site's access policy. It prints only structural key paths, primitive
types, collection sizes, byte count and SHA-256; it does **not** print
match/player names, scores, dates or other JSON values, and cannot
prove result accuracy or licensing. It rejects duplicate JSON keys,
invalid JSON, unknown roots and files over 2 MiB. It never writes to
PostgreSQL, R2, or a report file. Share only the structural report,
not the raw licensed feed, unless distribution rights are established.

This inventory is intentionally *not* an auto-discovered schema
normalizer. A future Codex review must inspect actual files,
document versioning and provenance, then separately approve
narrow field mappings and tests before activating any preview API.
The adapter remains default-disabled, and this PR is still Draft.

### Optional single permitted live probe (no SQL/R2)

If an operator has **independently reviewed** the access conditions, they
may run **one** manual bounded JSON GET through the existing safe adapter
without enabling an application endpoint:

```bash
cd backend
python -m app.providers.cornerflick_probe --season --allow-one-request --acknowledge-source-terms
python -m app.providers.cornerflick_probe --match-slug bayern-munchen-vs-1-fc-koln-bundesliga-2026-11-20 --allow-one-request --acknowledge-source-terms
```

These are **separate commands**, each performs at most one read; execute
only a permitted bounded probe (do not interpret the pair as permission to
mass download a season). The match slug above is grounded in the public
Cornerflick page. Neither CLI prints raw field values nor persists data.
A valid JSON response is still **NOT** proof of 90-minute result
semantics, reuse rights, or correctness. HTTP denials fail closed.
If no external request is authorized, use the offline file inspector
instead.

**Do not run this command in production, CI or a server/cron.** It is
a human-controlled diagnostic step only.
