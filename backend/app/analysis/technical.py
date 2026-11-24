"""Technical aggregates from verified past games and timestamped source observations."""
import math
from datetime import datetime
from app.analysis.engine import point_in_time


def technical_summary(team: str, matches: list[dict], *, as_of: datetime, venue: str | None = None) -> dict:
    groups, games = {}, set()
    eligible = [r for r in point_in_time(matches, as_of) if r.get("verified_at") is not None
                and str(r.get("status", "")).upper() in {"FINAL", "FINISHED", "FT"}]
    for match in eligible:
        kickoff = match.get("start_time") or match.get("match_start")
        if isinstance(kickoff, str):
            try:
                kickoff = datetime.fromisoformat(kickoff.replace("Z", "+00:00"))
            except ValueError:
                continue
        if not isinstance(kickoff, datetime) or kickoff.tzinfo is None or kickoff > as_of:
            continue
        home = match.get("home_team") == team
        if not home and match.get("away_team") != team:
            continue
        if venue and ((venue == "home") != home):
            continue
        # Only observations actually available at the analysis time can be used.
        observations = [{**row, "available_at": (row.get("available_at") or row.get("retrieved_at")
                         or row.get("captured_at") or row.get("created_at"))}
                        for row in match.get("technical_statistics", [])]
        stats = point_in_time(observations, as_of)
        latest = {}
        for observation in stats:
            stamp = (observation.get("available_at") or observation.get("retrieved_at")
                     or observation.get("captured_at") or observation.get("created_at"))
            if isinstance(stamp, str):
                stamp = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
            if not isinstance(stamp, datetime):
                continue
            metric = (observation.get("name"), observation.get("period"), observation.get("unit"))
            previous = latest.get(metric)
            if previous is None or stamp > previous[0]:
                latest[metric] = (stamp, observation)
            elif stamp == previous[0] and any(previous[1].get(k) != observation.get(k) for k in ("home_value", "away_value")):
                latest[metric] = (stamp, {**observation, "home_value": None, "away_value": None})
        for _, row in latest.values():
            value = row.get("home_value" if home else "away_value")
            if value is None or isinstance(value, bool) or not row.get("name") or not row.get("source_url"):
                continue
            try:
                value = float(value)
            except (TypeError, ValueError):
                continue
            if not math.isfinite(value) or value < 0:
                continue
            key = (row["name"], row.get("period"), row.get("unit"))
            group = groups.setdefault(key, {"values": [], "sources": set()})
            group["values"].append(value)
            group["sources"].add(row["source_url"])
            games.add((match.get("provider"), match.get("provider_match_id")))
    return {"status": "AVAILABLE" if groups else "INSUFFICIENT", "sample_size": len(games),
            "metrics": [{"name": name, "period": period, "unit": unit,
                         "average": sum(group["values"])/len(group["values"]),
                         "sample_size": len(group["values"]), "source_urls": sorted(group["sources"])}
                        for (name, period, unit), group in sorted(groups.items(), key=lambda item:str(item[0]))]}
