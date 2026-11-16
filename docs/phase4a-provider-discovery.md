# Phase 4A — Provider Discovery and Smoke-Test Readiness

**Repository state checked:** 2026-11-16, `main` at `3eb1aa2`.

## Decision

No provider is selected or connected. The repository's `.env` has no provider name, base URL, key, enabled league/season, PostgreSQL URL, or R2 credentials. The process environment also has no provider or storage credentials. The provider interface is already generic and the Phase 3 ingestion/archive path remains the only implementation; this phase did not add a vendor adapter or make a live request.

**Recommended first account to evaluate: Sportmonks Football API 3.** It is the strongest single-vendor candidate found for matches, seasons/results, and odds in one football API. The official docs describe fixture/season endpoints and standard/Premium pre-match odds. Premium odds record opening values and subsequent changes, but are retained only until seven days after kickoff. Sportmonks says the historical season catalog is subscription-specific and its historical data is still being integrated. Therefore it does not meet full 2024-25/2025-26 odds backfill on the documented retention window, and actual league/season/market coverage must be checked using an account before an adapter is written.

The recommended candidate is **not** verified as an İddaa source. Treat all returned bookmaker prices as provider/bookmaker odds, identified by the actual bookmaker name. Do not label them İddaa odds unless the provider's live bookmaker catalogue and the exact source identity establish that.

### Provider comparison

| Candidate | Verified in official docs | Material gap for this project |
|---|---|---|
| Sportmonks Football API 3 | Football seasons/fixtures; standard and Premium odds; Premium keeps opening and changed pre-match odds, with bookmaker and market entities. | Premium odds history is only available through 7 days after kickoff. Historical seasons are account-specific and documentation says historical data is still being added. No İddaa bookmaker verification. Best single-provider candidate, not yet selected/configured. |
| The Odds API | Explicit Turkish Süper Lig coverage; 1X2, handicap and totals; historical Süper Lig odds from June 2020 on paid plans; scores for completed matches up to 3 days old. | The page describes odds from bookmakers by region, not İddaa. Results are not documented there as a season-long results archive. Would likely need a separate match/results provider. |
| API-Football / API-Sports | Football fixtures/seasons, results, league coverage metadata and bookmaker/market endpoints. Pre-match odds are available 1–14 days before kickoff; API docs state a 7-day odds history. | Does not provide long-retention odds history for full-season historical backfill. League/season odds coverage varies and requires account-level checks. |

Official documentation reviewed:

- [Sportmonks seasons](https://docs.sportmonks.com/v3/endpoints-and-entities/endpoints/seasons) and [fixtures](https://docs.sportmonks.com/v3/tutorials-and-guides/tutorials/livescores-and-fixtures/fixtures)
- [Sportmonks Premium fixture/market odds and seven-day retention](https://docs.sportmonks.com/v3/endpoints-and-entities/endpoints/premium-odds-feed/premium-pre-match-odds/get-premium-odds-by-fixture-id-and-market-id), [historical odds endpoint](https://docs.sportmonks.com/v3/endpoints-and-entities/endpoints/premium-odds-feed/premium-pre-match-odds/get-all-historical-odds), and [rate limits](https://docs.sportmonks.com/v3/api/rate-limit)
- [The Odds API Turkish Süper Lig coverage](https://the-odds-api.com/sports/turkish-super-lig-odds.html) and [bookmakers by region](https://the-odds-api.com/sports-odds-data/bookmaker-apis.html)
- [API-Football v3 documentation](https://www.api-football.com/documentation-v3)

## Capability assessment

No `PASS` here means live access verified. Plan-dependent details, exact league IDs, seasons, bookmakers, pagination, and account quota remain unverified until an account is configured. The statuses below reflect documented support plus these limits.

| Capability | Status | Evidence / limitation |
|---|---|---|
| 2024-25 matches | PARTIAL | Sportmonks has historical season/fixture APIs, but availability is by subscription and it says historical data is still being integrated. No account query was possible. |
| 2024-25 results | PARTIAL | Fixture/result API surface exists; exact season coverage and final-score completeness are unverified. |
| 2024-25 odds | FAIL for Sportmonks backfill | Premium historic updates age out seven days after kickoff. The Odds API advertises Turkish Süper Lig odds history from June 2020 on paid plans, but its bookmaker source is not verified as İddaa and this would require a separate results source. |
| 2025-26 matches | PARTIAL | Subscription-dependent season coverage; not queried. |
| 2025-26 results | PARTIAL | Subscription-dependent fixture/result coverage; not queried. |
| 2025-26 odds | FAIL for Sportmonks backfill | Same seven-day Premium odds retention constraint. |
| 2026-27 live matches | PARTIAL | Live/fixture endpoints exist; Turkish league entitlement and actual response are unverified. |
| 2026-27 live odds | PARTIAL | Premium/live odds endpoints exist; Turkish league/market/bookmaker entitlement is unverified. |
| Historical odds | PARTIAL | Sportmonks retains and exposes changed values only within seven days after kickoff. The Odds API advertises older Turkish Süper Lig history on paid plans, with no İddaa source verified. |
| Pagination | PARTIAL | Documented endpoints paginate; maxima and page counts differ by endpoint. No adapter/account response was available to validate page traversal. |
| Rate limits | PARTIAL | Sportmonks documents hourly per-entity limits and response usage metadata; limits vary by plan/add-ons. Exact account quota is unknown. |
| İddaa source | UNVERIFIED | No official evidence reviewed confirms exact İddaa odds from these candidate APIs. |

## Smoke-test status

The requested one-league/one-day test was **not run** because no API key, provider account, or league ID is configured. No provider response was received, so matches, odds, results, and error counts are unavailable (not zero). Do not run historical ingestion until a real provider call succeeds and the test below passes.

| Check | Status |
|---|---|
| Provider API | `PROVIDER_NOT_CONFIGURED` |
| Provider credentials | `PROVIDER_NOT_CONFIGURED` |
| League/date response | NOT RUN |
| Match and odds normalization from provider response | NOT RUN |
| Result normalization/finalization | NOT RUN |
| R2 upload, checksum, row count, Parquet readback | `R2_NOT_CONFIGURED` |
| PostgreSQL migrations/upsert/idempotency | `POSTGRES_NOT_CONFIGURED` |
| Docker container run | NOT RUN; Docker Engine availability not established in this discovery pass |

The repository already has `test_historical_query_excludes_future_odds_snapshot`, which confirms a query cutoff at 15:00 returns a 14:00 snapshot and excludes a 17:00 snapshot. This protects historical queries from lookahead; it does not validate vendor timestamps or a live API response.

## Request and storage estimate before any backfill

The current date-by-date historical job calls `get_leagues()` once, then `get_matches(date)` and `get_results(date)` for every day. Its baseline is therefore **1 + 2 × number of calendar dates**, for one league, before provider pagination, retries, or other requests. For illustrative complete date windows of 2024-08-01–2025-06-30 (334 days), 2025-08-01–2026-06-30 (334 days), and 2026-07-01–2026-11-16 (139 days), the baseline would be **1,615 API calls**. At the app's configured default limiter of 60 calls/minute this is a theoretical minimum of about **27 minutes**, but the actual vendor quota, latency, pagination, and backoff can increase it. This job does not fetch historical odds, so those requests are not included.

Estimated provider usage cannot be priced without a plan and its remaining quota. Estimated R2 storage cannot be responsibly calculated without a real response sample and Parquet size. No historical loop was launched. Run a one-day smoke first, then measure normalized row counts and compressed bytes before estimating a larger archive.

## Required next inputs

1. A Sportmonks test/production token (or a different provider explicitly selected by the account owner) and its plan name.
2. The intended Turkish league ID(s) and season IDs as returned by that account.
3. Confirmation of which bookmakers/markets must count as acceptable; specifically, whether generic provider bookmakers are acceptable while İddaa remains unavailable.
4. PostgreSQL and R2 credentials if those smoke checks are required in the same run.

After these are configured, implement only the documented adapter, run one selected league/date once, verify odds snapshots are timestamped and idempotent, archive a small Parquet partition and read it back, verify PostgreSQL upsert twice, then decide whether any bounded historical ingestion is justified. Do not start a full backfill in Phase 4A.
