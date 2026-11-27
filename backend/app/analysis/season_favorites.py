"""Compare timestamped, pre-kickoff 1-X-2 quotes with verified 90-minute scores."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import math

MIN_SAMPLE = 200
BINS = ((1, 1.5), (1.5, 2), (2, 3), (3, math.inf))
OUTCOMES = ("win", "draw", "upset")


def _utc(value: str) -> datetime:
    stamp = datetime.fromisoformat(value)
    if stamp.utcoffset() is None:
        raise ValueError("ODDS_TIMESTAMP_REQUIRES_OFFSET")
    return stamp.astimezone(timezone.utc)


def validate_odds(observation: dict, fixtures: dict[str, dict]) -> dict:
    fixture = fixtures.get(observation.get("fixture_id"))
    if not fixture or not fixture["kickoff_utc"]:
        raise ValueError("ODDS_FIXTURE_UNRESOLVED")
    observed = _utc(observation["observed_at"])
    if observed > datetime.now(timezone.utc) or observed >= _utc(fixture["kickoff_utc"]):
        raise ValueError("ODDS_NOT_PRE_MATCH")
    if observation.get("market") != "1X2_90" or observation.get("kind") not in {"OPENING", "CLOSING", "OBSERVED"}:
        raise ValueError("UNSUPPORTED_ODDS_MARKET")
    prices = observation.get("prices")
    if (not isinstance(prices, (list, tuple)) or len(prices) != 3 or
            any(type(v) not in (int, float) or not 1 < v <= 10000 for v in prices)):
        raise ValueError("INVALID_ODDS_PRICES")
    names = {field: observation.get(field) for field in ("provider", "bookmaker", "source_id")}
    if any(not isinstance(value, str) or not value.strip() or len(value) > 160 for value in names.values()):
        raise ValueError("ODDS_PROVENANCE_REQUIRED")
    return {"fixture_id": fixture["id"], **names, "market": "1X2_90", "kind": observation["kind"],
            "observed_at": observed.isoformat(), "prices": [float(v) for v in prices]}


def analyze_favorites(fixtures: list[dict], observations: list[dict]) -> dict:
    index = {row["id"]: row for row in fixtures}
    quotes = defaultdict(list)
    for observation in observations:
        try:
            quote = validate_odds(observation, index)
        except (ValueError, KeyError, TypeError):
            continue
        quotes[quote["fixture_id"]].append(quote)
    samples = []
    for fixture_id, candidates in quotes.items():
        row = index[fixture_id]
        if not row.get("verified_at") or row["status"] != "FINISHED" or row["regulation"] is None:
            continue
        # Pick the latest complete quote. Lexical tie-breaks make the choice reproducible.
        quote = max(candidates, key=lambda q: (q["observed_at"], q["kind"] == "CLOSING",
                                              q["provider"], q["bookmaker"], q["source_id"]))
        home, draw, away = quote["prices"]
        if home == away:
            continue
        fav, other = (home, away) if home < away else (away, home)
        weights = [1 / price for price in (fav, draw, other)]
        expected = [v / sum(weights) for v in weights]
        h, a = row["regulation"]
        outcome = "draw" if h == a else "win" if (h > a) == (home < away) else "upset"
        bucket = next(i for i, (lower, upper) in enumerate(BINS) if lower <= fav < upper)
        samples.append((row["season"], bucket, outcome, expected))

    def measure(items):
        n = len(items)
        if not n:
            return {"sample": 0}
        result = {"sample": n}
        for i, name in enumerate(OUTCOMES):
            actual = sum(item[2] == name for item in items) / n
            expected = sum(item[3][i] for item in items) / n
            result[name] = {"actual": actual, "expected": expected, "difference": actual - expected}
        return result

    result = {"odds_matches": len(quotes), "sample": len(samples), "minimum_sample": MIN_SAMPLE,
              "status": "SUFFICIENT" if len(samples) >= MIN_SAMPLE else "INSUFFICIENT_SAMPLE"}
    if len(samples) < MIN_SAMPLE:
        return result
    by_season = defaultdict(list)
    for sample in samples:
        by_season[sample[0]].append(sample)
    result.update(measure(samples))
    result["bins"] = [
        {"lower": lower, "upper": upper if math.isfinite(upper) else None,
         **measure([item for item in samples if item[1] == i])}
        for i, (lower, upper) in enumerate(BINS)
    ]
    result["seasons"] = {season: measure(rows) for season, rows in sorted(by_season.items())}

    # Fit favorite win frequencies on earlier seasons only, then evaluate next-season Brier scores.
    prior = []
    folds = []
    for season, rows in sorted(by_season.items()):
        if len(prior) >= MIN_SAMPLE:
            overall = measure(prior)["win"]["actual"]
            rates = {}
            for bucket in range(len(BINS)):
                group = [item for item in prior if item[1] == bucket]
                rates[bucket] = measure(group)["win"]["actual"] if group else overall
            folds.append({"test_season": season, "train_sample": len(prior), "test_sample": len(rows),
                          "historical_brier": sum((rates[bucket] - (outcome == "win")) ** 2
                                                  for _, bucket, outcome, _ in rows) / len(rows),
                          "market_brier": sum((expected[0] - (outcome == "win")) ** 2
                                              for _, _, outcome, expected in rows) / len(rows)})
        prior.extend(rows)
    result["backtest"] = folds
    return result
