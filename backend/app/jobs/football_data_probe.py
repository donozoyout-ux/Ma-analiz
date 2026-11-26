"""Read-only Bundesliga corroboration probe using official source APIs.

Run: python -m app.jobs.football_data_probe --day 2026-11-20 --matchday 11
No SQL/R2 connectivity or writes. FOOTBALL_DATA_ORG_API_TOKEN is required.
"""
import argparse
import json
from datetime import date, datetime, timezone
import httpx

from app.providers.football_data_org import FootballDataFailure, fetch_bundesliga_finals
from app.providers.result_sources import parse_openligadb
from app.analysis.football_data_corroboration import corroborate


def run(day: date, matchday: int, *, token=None, openligadb_payload=None, secondary_payload=None):
    if not 1 <= matchday <= 34 or day.year not in (2026, 2027):
        raise ValueError("INVALID_BUNDESLIGA_SCOPE")
    season = day.year if day.month >= 7 else day.year - 1
    observed_at = datetime.now(timezone.utc)
    url = f"https://api.openligadb.de/getmatchdata/bl1/{season}/{matchday}"
    if openligadb_payload is None:
        try:
            with httpx.Client(timeout=8, follow_redirects=False) as client:
                response = client.get(url)
                response.raise_for_status()
                if len(response.content) > 512_000:
                    raise ValueError("OPENLIGADB_RESPONSE_TOO_LARGE")
                openligadb_payload = response.json()
        except (httpx.HTTPError, ValueError):
            raise ValueError("OPENLIGADB_PROBE_UNAVAILABLE") from None
    primary = parse_openligadb(openligadb_payload, day, observed_at=observed_at, source_url=url)
    secondary = (fetch_bundesliga_finals(day, token=token, observed_at=observed_at)
                 if secondary_payload is None else
                 __import__("app.providers.football_data_org", fromlist=["parse_bundesliga_finals"])
                    .parse_bundesliga_finals(secondary_payload, day=day, observed_at=observed_at))
    decision = corroborate(primary, secondary)
    return {"date": day.isoformat(), "matchday": matchday, "league": "de.bl1",
            "source_count": 2, "primary_count": len(primary), "secondary_count": len(secondary),
            "decision": decision, "sql": "NOT_USED", "r2": "NOT_USED",
            "verified_at_changed": False, "auto_delete_enabled": False,
            "terms": "football-data.org official API; attribution required; no redistribution through this probe"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--day", type=date.fromisoformat, required=True)
    parser.add_argument("--matchday", type=int, required=True)
    args = parser.parse_args(argv)
    try:
        result = run(args.day, args.matchday)
    except (FootballDataFailure, ValueError) as exc:
        print(json.dumps({"status": str(exc), "writes": False}))
        return 2
    print(json.dumps(result, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
