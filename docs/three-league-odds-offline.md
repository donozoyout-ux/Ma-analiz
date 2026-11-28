# Bundesliga, La Liga, Premier League — offline 1X2 calibration pilot

Status: **BLOCKED_HISTORICAL_ODDS_OR_LICENCE**. Historical closing odds with verified rights to the intended storage, public display, commercial analytics and redistribution have not been imported, so no real league performance ranking is produced.

This first phase adds pure `app.analytics.three_league_odds_offline.assess(records, permissions)`, unit tests with invented fixtures and a scoped offline CI workflow. It makes no HTTP calls, consumes **zero** The Odds API credits, writes no SQL/R2 files, and is not registered as a production route, worker or CLI.

## Source schema and controls

Each record: `league` (`de.bl1`/`es.1`/`en.pl`), `season` (`2024-2025`/`2025-2026`/`2026-2027`), `match_id`, `source`, `kickoff_utc`, `odds_captured_utc`, `odds_stage=closing`, `status=finished`, integer nonnegative `home_score`/`away_score`, and three >1 finite decimal odds under `odds.home`, `odds.draw`, `odds.away`. All timestamps require timezone; only historical matches through 2026-11-28 are permitted. Closing observations after kickoff are excluded. Source permissions must explicitly review analysis, storage, redistribution and commercial uses and include an HTTPS licence reference. The caller must verify authenticity externally; the mere existence of permission fields is not legal proof.

Return results show the number of admitted records, omitted record categories, mean 1X2 bookmaker overround, favorite win/draw/loss rates, 3-class Brier and log loss and small probability bins. The team favorite is the stronger **home/away** selection; draws are tracked separately and never construed as match manipulation. No inferred match-fixing labels.

The calibration pilot **always** returns no league ranking because public-licence clearance and comparable source coverage are not yet established. The Odds API is never queried and the 500-credit budget is untouched. Team names and provider ids require separate identity reconciliation before any real records. A proper future study needs comparable season coverage, sample uncertainty, opening/closing stratification, holdout evaluation and licensing evidence.

## Deployment and data safety

No automatic collection, credentials, .env loading, R2 write, migration, live deploy, merge, inference of missing odds, or source-term circumvention. Existing Bundesliga 708-match R2 archive stays unchanged. PRs #57, #60, #61 are independent.
