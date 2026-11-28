"""Aggregate source-reported corners/yellow/red cards with independent denominators.

No external odds, inference of missing values, or score verification.
"""
from __future__ import annotations
from typing import Iterable, Mapping, Any


def summarize(rows: Iterable[Mapping[str, Any]], *, team: str = "") -> dict:
    totals = {"corners": 0, "yellows": 0, "reds": 0}
    samples = {"corners": 0, "yellows": 0, "reds": 0}
    own = {"corners": 0, "yellows": 0, "reds": 0}
    against = {"corners": 0, "yellows": 0, "reds": 0}
    count = 0
    for row in rows:
        home, away = row["home_team"], row["away_team"]
        is_home = bool(team) and home.casefold() == team.casefold()
        is_away = bool(team) and away.casefold() == team.casefold()
        if team and not (is_home or is_away):
            continue
        count += 1
        for metric, left, right in (
            ("corners", "home_corners", "away_corners"),
            ("yellows", "home_yellows", "away_yellows"),
            ("reds", "home_reds", "away_reds"),
        ):
            a, b = row[left], row[right]
            if type(a) is not int or type(b) is not int or a < 0 or b < 0:
                continue
            samples[metric] += 1
            totals[metric] += a + b
            if team:
                own[metric] += a if is_home else b
                against[metric] += b if is_home else a
    return {
        "match_count": count,
        "metrics": {
            key: {
                "sample": samples[key],
                "average_total": round(totals[key] / samples[key], 2) if samples[key] else None,
                "average_for": round(own[key] / samples[key], 2) if samples[key] and team else None,
                "average_against": round(against[key] / samples[key], 2) if samples[key] and team else None,
            } for key in totals
        },
    }
