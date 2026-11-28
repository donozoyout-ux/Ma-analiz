"""Offline, nullable statistics matching and immutable archive preparation."""
from collections import Counter
from datetime import date, datetime, timezone
import hashlib
import json
from urllib.parse import urlsplit

from app.jobs.bundesliga_season_r2 import match_key
from app.storage.r2 import parquet_bytes, parquet_rows

METRICS = ("corners", "corners_ht", "yellow_cards", "red_cards", "shots", "shots_on_target",
           "possession", "fouls", "offsides")


def valid_permission(permission: dict) -> bool:
    """Allow only explicit reviewed rights covering the intended published archive."""
    ref = permission.get("reference", "")
    parsed = urlsplit(ref)
    return (permission.get("review_status") == "APPROVED" and permission.get("storage") is True
            and permission.get("redistribution") is True and permission.get("commercial") is True
            and isinstance(permission.get("source"), str) and bool(permission["source"].strip())
            and isinstance(permission.get("licence"), str) and bool(permission["licence"].strip())
            and isinstance(permission.get("attribution"), str) and bool(permission["attribution"].strip())
            and parsed.scheme == "https" and bool(parsed.hostname)
            and not parsed.username and not parsed.password)


def build_pilot(fixtures: list[dict], observations: list[dict], *, season: str, permission: dict) -> dict:
    if season not in {"2024-2025", "2025-2026", "2026-2027"}:
        raise ValueError("UNSUPPORTED_SEASON")
    index = {}
    for row in fixtures:
        if row.get("season") != season or row.get("league") != "de.bl1" or not row.get("id"):
            raise ValueError("FIXTURE_SCOPE_MISMATCH")
        key = match_key(row["date"], row["home_team"], row["away_team"])
        if key in index:
            raise ValueError("AMBIGUOUS_FIXTURE")
        index[key] = row["id"]
    matched, rejected = [], Counter()
    used_source, used_match = set(), set()
    for obs in observations:
        if obs.get("season") != season or obs.get("league") != "de.bl1":
            rejected["SCOPE_MISMATCH"] += 1
            continue
        try:
            key = match_key(obs["date"], obs["home_team"], obs["away_team"])
            date.fromisoformat(obs["date"])
            timestamp = datetime.fromisoformat(obs["observed_at"].replace("Z", "+00:00"))
            if timestamp.utcoffset() is None:
                raise ValueError
            observed = timestamp.astimezone(timezone.utc).isoformat()
        except (KeyError, TypeError, ValueError):
            rejected["INVALID_IDENTITY_OR_TIME"] += 1
            continue
        source, source_id = obs.get("source"), obs.get("source_match_id")
        if not isinstance(source, str) or not source.strip() or not isinstance(source_id, str) or not source_id.strip():
            rejected["MISSING_SOURCE_ID"] += 1
            continue
        match_id = index.get(key)
        if not match_id:
            rejected["UNMATCHED_IDENTITY"] += 1
            continue
        if (source, source_id) in used_source or match_id in used_match:
            raise ValueError("DUPLICATE_OR_AMBIGUOUS_OBSERVATION")
        statistics = obs.get("statistics")
        if not isinstance(statistics, dict):
            raise ValueError("INVALID_STATISTICS")
        values = {}
        for metric in METRICS:
            for side in ("home", "away"):
                field = f"{side}_{metric}"
                val = statistics.get(field)
                if val is not None:
                    if metric == "possession":
                        if type(val) not in (float, int) or not 0 <= val <= 100:
                            raise ValueError("INVALID_STATISTIC")
                    elif type(val) is not int or not 0 <= val <= 2147483647:
                        raise ValueError("INVALID_STATISTIC")
                values[field] = val
        used_source.add((source, source_id))
        used_match.add(match_id)
        matched.append({"match_id": match_id, "league": "de.bl1", "season": season,
                        "source": source, "source_match_id": source_id, "observed_at": observed,
                        "licence": permission.get("licence"), "permission_reference": permission.get("reference"),
                        **values})
    coverage = {metric: sum(all(row[f"{side}_{metric}"] is not None for side in ("home", "away"))
                            for row in matched) for metric in METRICS}
    incomplete = sum(any(row[f"{side}_{metric}"] is None for side in ("home", "away") for metric in METRICS)
                     for row in matched)
    licensed = valid_permission(permission) and all(row["source"] == permission["source"] for row in matched)
    result = {"status": "READY_FOR_REVIEW" if licensed else "BLOCKED_SOURCE_OR_LICENCE",
              "fixtures": len(fixtures), "matched": len(matched), "rejected": dict(rejected),
              "coverage": coverage, "matched_incomplete": incomplete, "unmatched_fixtures": len(fixtures)-len(matched),
              "archive": None}
    if matched:
        matched.sort(key=lambda row: (row["match_id"], row["source"], row["source_match_id"]))
        payload = parquet_bytes(matched)
        if parquet_rows(payload) != matched:
            raise RuntimeError("PARQUET_READBACK_MISMATCH")
        sha = hashlib.sha256(payload).hexdigest()
        key = f"statistics/bundesliga/season={season}/matches/{sha}.parquet"
        manifest = {"season": season, "league": "de.bl1", "row_count": len(matched), "coverage": coverage,
                    "permission": permission, "parquet": {"key": key, "sha256": sha, "size": len(payload)}}
        manifest_bytes = json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        manifest_hash = hashlib.sha256(manifest_bytes).hexdigest()
        result["archive"] = {"parquet_key": key, "parquet_sha256": sha,
                             "manifest_key": f"statistics/bundesliga/season={season}/manifests/{manifest_hash}.json",
                             "manifest_sha256": manifest_hash, "bytes": len(payload) + len(manifest_bytes)}
    return result


def summarize(rows: list[dict], metric: str) -> dict:
    """Count complete home/away pairs independently for each requested metric."""
    if metric not in METRICS:
        raise ValueError("UNSUPPORTED_METRIC")
    pairs = [(row.get(f"home_{metric}"), row.get(f"away_{metric}")) for row in rows]
    complete = [(home, away) for home, away in pairs if home is not None and away is not None]
    return {"metric": metric, "sample_size": len(complete), "missing": len(rows) - len(complete),
            "home_average": sum(home for home, _ in complete) / len(complete) if complete else None,
            "away_average": sum(away for _, away in complete) / len(complete) if complete else None}
