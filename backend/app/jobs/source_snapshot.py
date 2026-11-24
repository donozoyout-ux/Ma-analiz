"""Scheduled, read-only OpenLigaDB fixture snapshots.

Stores only provider observations in a GitHub Actions artifact. This is NOT
PostgreSQL/R2 archival. No odds, statistics, paywalled data or write secrets.
Run from repository root: python -m app.jobs.source_snapshot --output DIR
with working directory backend, to create a bounded 2026/27 source snapshot.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import time
from typing import Any, Callable
from zoneinfo import ZoneInfo

from app.providers.openligadb import ATTRIBUTION, LICENCE, LICENCE_URL, OpenLigaDBProvider

ISTANBUL = ZoneInfo("Europe/Istanbul")
COMPETITIONS = ("bl1", "ucl", "uel2026", "uecl2026")
MAX_MATCHES_PER_COMPETITION = 120


def collect(day: date, *, provider_factory: Callable[..., Any] = OpenLigaDBProvider,
            sleep: Callable[[float], None] = time.sleep, pace_seconds: int = 60) -> dict:
    """Four source requests with global provider pacing and isolated failures."""
    if not date(2026, 7, 4) <= day <= date(2027, 6, 23):
        raise ValueError("DATE_OUTSIDE_PILOT_SEASON")
    if not 0 <= pace_seconds <= 120:
        raise ValueError("INVALID_PACING")
    start = day - timedelta(days=3)
    end = day + timedelta(days=7)
    failures = []
    matches = []
    observed = datetime.now(timezone.utc).isoformat()
    for index, shortcut in enumerate(COMPETITIONS):
        if index:
            sleep(pace_seconds)
        try:
            with provider_factory(max_retries=1) as provider:
                rows = provider.get_competition_window(shortcut, start, end)
            if len(rows) > MAX_MATCHES_PER_COMPETITION:
                raise ValueError("SOURCE_SCOPE_OVERFLOW")
            for m in rows:
                kickoff = m["start_time"]
                if not isinstance(kickoff, datetime) or kickoff.tzinfo is None:
                    raise ValueError("INVALID_KICKOFF")
                if not m.get("provider_match_id") or not m.get("home_team") or not m.get("away_team"):
                    raise ValueError("INVALID_MATCH")
                score = m.get("score")
                if score is not None and (not isinstance(score, (list, tuple)) or len(score) != 2 or
                        any(not isinstance(v, int) or isinstance(v, bool) or v < 0 for v in score)):
                    raise ValueError("INVALID_SCORE")
                matches.append({
                    "source": "OpenLigaDB",
                    "provider_match_id": str(m["provider_match_id"]),
                    "league_id": m["league_id"],
                    "league_name": m["league_name"],
                    "season": m.get("season"),
                    "home_team": m["home_team"],
                    "away_team": m["away_team"],
                    "start_time": kickoff.isoformat(),
                    "status": m["status"],
                    "score": list(score) if score is not None else None,
                })
        except Exception as exc:
            # Preserve an explicit source-failure marker; never forge empty fixtures.
            failures.append({"competition": shortcut, "code": type(exc).__name__})
    unique: dict[tuple[str, str], dict] = {}
    for m in matches:
        key = (m["league_id"], m["provider_match_id"])
        if key in unique and unique[key] != m:
            failures.append({"competition": m["league_id"], "code": "CONFLICTING_MATCH_ID"})
            unique.pop(key, None)
            continue
        unique[key] = m
    ordered = sorted(unique.values(), key=lambda x:(x["start_time"], x["league_id"], x["provider_match_id"]))
    return {
        "snapshot_version": 1,
        "observed_at": observed,
        "requested_day_istanbul": day.isoformat(),
        "window_start_istanbul": start.isoformat(),
        "window_end_istanbul": end.isoformat(),
        "source": "OpenLigaDB",
        "attribution": ATTRIBUTION,
        "licence": LICENCE,
        "licence_url": LICENCE_URL,
        "storage": "GITHUB_ACTIONS_ARTIFACT_ONLY",
        "r2_written": False,
        "postgres_written": False,
        "failures": failures,
        "counts": {"matches": len(ordered), "finished": sum(x["status"]=="FINISHED" for x in ordered),
                   "scheduled": sum(x["status"]=="SCHEDULED" for x in ordered),
                   "live_observed": sum(x["status"]=="LIVE" for x in ordered)},
        "matches": ordered,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--day", type=date.fromisoformat,
                        default=datetime.now(ISTANBUL).date())
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    result = collect(args.day)
    args.output.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    checksum = sha256(raw).hexdigest()
    (args.output / "source-observations.json").write_bytes(raw)
    (args.output / "source-observations.sha256").write_text(
        f"{checksum}  source-observations.json\n", encoding="utf-8")
    print(json.dumps({"source": result["source"], "counts": result["counts"],
                      "failure_count": len(result["failures"]), "artifact_sha256": checksum,
                      "postgres_written": False, "r2_written": False}, ensure_ascii=False))
    return 0 if len(result["failures"]) == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
