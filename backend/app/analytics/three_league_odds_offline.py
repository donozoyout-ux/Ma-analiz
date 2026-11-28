"""Pure offline 1X2 calibration. No provider requests, SQL or object storage."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import math

LEAGUES = ("de.bl1", "es.1", "en.pl")
SEASONS = ("2024-2025", "2025-2026", "2026-2027")
CUTOFF = datetime(2026, 11, 28, 23, 59, 59, tzinfo=timezone.utc)
OUTCOMES = ("home", "draw", "away")


def utc(value: str) -> datetime:
    stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if stamp.utcoffset() is None:
        raise ValueError("NAIVE_TIME")
    return stamp.astimezone(timezone.utc)


def assess(records: list[dict], permissions: dict, *, cutoff=CUTOFF) -> dict:
    """Fail closed for source permissions and invalid or duplicate match identities.

    A permission record must explicitly allow the specific analysis and intended
    storage/publication uses. No datasets are fetched or persisted by this module.
    """
    if not isinstance(records, list):
        raise ValueError("INVALID_RECORDS")
    aggregate = defaultdict(list)
    rejected = defaultdict(int)
    seen = set()
    for row in records:
        if not isinstance(row, dict):
            rejected["invalid_record"] += 1
            continue
        league, season = row.get("league"), row.get("season")
        if league not in LEAGUES or season not in SEASONS:
            rejected["unsupported_scope"] += 1
            continue
        match_id = row.get("match_id")
        if not isinstance(match_id, str) or not match_id.strip():
            rejected["missing_match_id"] += 1
            continue
        key = (league, season, match_id)
        if key in seen:
            raise ValueError("DUPLICATE_MATCH")
        seen.add(key)
        source = row.get("source")
        grant = permissions.get(source) if isinstance(permissions, dict) else None
        if not isinstance(grant, dict) or not all(grant.get(field) is True for field in
                ("reviewed", "analysis", "storage", "redistribution", "commercial")):
            rejected["source_permission_unverified"] += 1
            continue
        if not isinstance(grant.get("licence_reference"), str) or not grant["licence_reference"].startswith("https://"):
            rejected["source_permission_unverified"] += 1
            continue
        try:
            kickoff, captured = utc(row["kickoff_utc"]), utc(row["odds_captured_utc"])
            odds = [float(row["odds"][label]) for label in OUTCOMES]
            score = (row["home_score"], row["away_score"])
            if any(type(v) is not int or v < 0 for v in score):
                raise ValueError("INVALID_SCORE")
            if any(not math.isfinite(o) or o <= 1 for o in odds):
                raise ValueError("INVALID_ODDS")
            if captured > kickoff or kickoff > cutoff:
                raise ValueError("FUTURE_OR_POSTMATCH_ODDS")
            if row.get("odds_stage") != "closing":
                raise ValueError("NOT_CLOSING_ODDS")
            if row.get("status") != "finished":
                raise ValueError("NOT_FINISHED")
        except (KeyError, TypeError, ValueError, OverflowError):
            rejected["invalid_or_incomplete"] += 1
            continue
        raw = [1 / o for o in odds]
        overround = sum(raw) - 1
        if overround < 0 or overround > 0.5:
            rejected["invalid_market_margin"] += 1
            continue
        probs = [p / sum(raw) for p in raw]
        actual = 0 if score[0] > score[1] else 1 if score[0] == score[1] else 2
        # Draw cannot be named the favourite team.
        favourite = 0 if probs[0] >= probs[2] else 2
        favorite_prob = probs[favourite]
        bucket = ("<50%" if favorite_prob < .5 else "<60%" if favorite_prob < .6 else
                  "<70%" if favorite_prob < .7 else "<80%" if favorite_prob < .8 else "80%+")
        aggregate[league].append((probs, actual, favourite, overround, bucket, season))
    leagues = {}
    for league in LEAGUES:
        rows = aggregate[league]
        if not rows:
            leagues[league] = {"status": "HESAPLANAMADI", "sample_size": 0}
            continue
        n = len(rows)
        wins = sum(actual == fav for _, actual, fav, _, _, _ in rows)
        draws = sum(actual == 1 for _, actual, _, _, _, _ in rows)
        losses = n - wins - draws
        groups = {}
        for group in ("<50%", "<60%", "<70%", "<80%", "80%+"):
            subset = [r for r in rows if r[4] == group]
            if subset:
                groups[group] = {"n": len(subset), "mean_expected": sum(p[f] for p, _, f, _, _, _ in subset) / len(subset),
                                 "observed": sum(actual == f for _, actual, f, _, _, _ in subset) / len(subset)}
        leagues[league] = {
            "status": "OBSERVED_SAMPLE_NOT_LEAGUE_RANKING", "sample_size": n,
            "favourite_win_rate": wins / n, "favourite_draw_rate": draws / n,
            "favourite_loss_rate": losses / n, "draw_rate": draws / n,
            "mean_overround": sum(m for _, _, _, m, _, _ in rows) / n,
            "brier_multiclass": sum(sum((p[j] - (actual == j)) ** 2 for j in range(3))
                                   for p, actual, _, _, _, _ in rows) / n,
            "log_loss": -sum(math.log(max(p[actual], 1e-15)) for p, actual, _, _, _, _ in rows) / n,
            "calibration": groups,
            "seasons": {season: sum(row[5] == season for row in rows) for season in SEASONS},
        }
    return {"mode": "OFFLINE_ONLY", "source_requests": 0, "odds_api_credits": 0,
            "leagues": leagues, "rejected": dict(rejected),
            "ranking": "HESAPLANAMADI_UNTIL_COMPARABLE_LICENSED_COVERAGE"}
