"""Retired paid-provider smoke entry point; no SQL, R2 or network access."""
import argparse
import json
import unicodedata
from datetime import date

def _fold(value: str) -> str:
    return "".join(char for char in unicodedata.normalize("NFKD", value.casefold())
                   if not unicodedata.combining(char))


def _find_turkish_super_lig(leagues: list[dict]) -> dict:
    matches = []
    for league in leagues:
        name = _fold(str(league.get("name") or ""))
        country = _fold(str(league.get("country") or ""))
        league_like = "super lig" in name or "superliga" in name
        turkey_like = country in {"turkey", "turkiye"} or "turkey" in name or "turkiye" in name
        if league_like and turkey_like:
            matches.append(league)
    if len(matches) != 1:
        raise ValueError("TURKISH_SUPER_LIG_NOT_FOUND" if not matches else "TURKISH_SUPER_LIG_AMBIGUOUS")
    return matches[0]


def run(day: date, *, persist: bool = False) -> dict:
    return {"provider": "SPORTMONKS", "smoke_status": "LIVE SMOKE TEST BLOCKED",
            "errors": ["LEGACY_PAID_PROBE_DISABLED"], "writes": False,
            "next_command": "python -m app.jobs.scraping_probe --league 8 --date YYYY-MM-DD"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", type=date.fromisoformat, required=True)
    parser.add_argument("--persist", action="store_true", help="Retired; never writes data")
    args = parser.parse_args()
    print(json.dumps(run(args.date, persist=args.persist)))
    raise SystemExit(2)


if __name__ == "__main__":
    main()
