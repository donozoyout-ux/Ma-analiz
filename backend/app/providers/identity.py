"""Conservative cross-source identity. No fuzzy club-name or kickoff guesses."""
import hashlib
import json
import unicodedata
from datetime import datetime, timezone

# Reviewed competition identifiers only. Team aliases require separate evidence.
LEAGUES = {("goaloo", "8"): "de.bl1", ("openfootball", "de.1"): "de.bl1",
           ("openligadb", "bl1"): "de.bl1", ("goaloo", "36"): "en.pl",
           ("openfootball", "en.1"): "en.pl"}
REVIEWED_IMPORT_KEY = "verified-import:de.bl1:2026-11-20:openligadb:83237"


def fixture_key(row: dict) -> str | None:
    reviewed = reviewed_fixture_key(row)
    if reviewed:
        return reviewed
    league = LEAGUES.get((str(row.get("provider", "")).lower(), str(row.get("league_id", ""))))
    if not league:
        return None
    start = row.get("start_time")
    if isinstance(start, str):
        try:
            start = datetime.fromisoformat(start.replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(start, datetime) or start.tzinfo is None or start.utcoffset() is None:
        return None
    def team(value):
        return " ".join(unicodedata.normalize("NFKC", str(value or "")).casefold().split())
    home, away = team(row.get("home_team")), team(row.get("away_team"))
    if not home or not away or home == away:
        return None
    return json.dumps([league, start.astimezone(timezone.utc).isoformat(), home, away], ensure_ascii=False)


def reviewed_fixture_key(row: dict) -> str | None:
    """One evidence-reviewed alias only; never a general name/league substitution.

    Both native source IDs, league, kickoff and ordered names must match the review
    in docs/reports/result-alias-review-2026-11-20.json. Used by the normal daily
    writer too, so it cannot insert a second Goaloo copy after the stage-3 import.
    """
    from app.analysis.result_verification import team_key
    from app.providers.result_sources import utc
    provider, identity = row.get("provider"), str(row.get("provider_match_id"))
    allowed = {("goaloo", "3018869"): {"8", "de.bl1"},
               ("openligadb", "83237"): {"bl1", "de.bl1"}}
    if str(row.get("league_id")) not in allowed.get((provider, identity), set()):
        return None
    start = utc(row.get("start_time"))
    if start != utc("2026-11-20T19:30:00Z"):
        return None
    pair = [team_key(provider, row.get(field), reviewed=True) for field in ("home_team", "away_team")]
    if pair != ["de.bayern-muenchen", "de.1-fc-koeln"]:
        return None
    return json.dumps(["de.bl1", start.isoformat(), *pair], ensure_ascii=False)


def lock_id(key: str) -> int:
    return int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], "big", signed=True)


def deduplicate_scope(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    unique, conflicts, seen = [], [], {}
    for row in rows:
        source_id = (row.get("provider"), str(row.get("provider_match_id")))
        key = fixture_key(row) or json.dumps(source_id)
        existing = seen.get(key)
        if existing is not None:
            conflicts.append({"source_identity": source_id, "matched_source_identity": existing,
                              "reason": "EXACT_FIXTURE_DUPLICATE"})
            continue
        seen[key] = source_id
        unique.append(row)
    return unique, conflicts
