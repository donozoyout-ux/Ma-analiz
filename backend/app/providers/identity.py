"""Conservative cross-source identity. No fuzzy club-name or kickoff guesses."""
import hashlib
import json
import unicodedata
from datetime import datetime, timezone

# Reviewed competition identifiers only. Team aliases require separate evidence.
LEAGUES = {("goaloo", "8"): "de.bl1", ("openfootball", "de.1"): "de.bl1",
           ("openligadb", "bl1"): "de.bl1", ("goaloo", "36"): "en.pl",
           ("openfootball", "en.1"): "en.pl"}


def fixture_key(row: dict) -> str | None:
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
