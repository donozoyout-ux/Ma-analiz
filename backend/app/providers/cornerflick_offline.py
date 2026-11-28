"""Synthetic offline matching only; Cornerflick's wire schema is unverified."""
from datetime import date
import hashlib

METRICS = ('corners', 'corners_ht', 'yellow_cards', 'red_cards', 'shots', 'shots_on_target')
SEASONS = {'2024-2025', '2025-2026', '2026-2027'}

def verify_bytes(data, sha):
    if not isinstance(sha, str) or len(sha) != 64 or hashlib.sha256(data).hexdigest() != sha:
        raise ValueError('SHA256_MISMATCH')

def identity(row, season):
    if row.get('season') != season or row.get('league') != 'de.bl1':
        raise ValueError('SCOPE_MISMATCH')
    try:
        day = date.fromisoformat(row['date'])
        start = int(season[:4])
        if not date(start, 7, 1) <= day < date(start + 1, 7, 1) or day > date(2026, 11, 28):
            raise ValueError
        home, away = row['home_team'], row['away_team']
        if not isinstance(home, str) or not isinstance(away, str) or not home.strip() or not away.strip() or home.strip().casefold() == away.strip().casefold():
            raise ValueError
        return day, home.strip().casefold(), away.strip().casefold()
    except (KeyError, TypeError, ValueError):
        raise ValueError('INVALID_IDENTITY_OR_DATE') from None

def match_synthetic(fixtures, observations, season):
    if season not in SEASONS:
        raise ValueError('UNSUPPORTED_SEASON')
    index, fixture_ids = {}, set()
    for row in fixtures:
        key, mid = identity(row, season), row.get('id')
        if not isinstance(mid, str) or not mid.strip() or key in index or mid in fixture_ids:
            raise ValueError('AMBIGUOUS_FIXTURE')
        index[key] = mid
        fixture_ids.add(mid)
    used_sources, used_matches, result = set(), set(), []
    for row in observations:
        key, sid = identity(row, season), row.get('source_match_id')
        if not isinstance(sid, str) or not sid.strip() or sid in used_sources:
            raise ValueError('DUPLICATE_OR_MISSING_SOURCE_ID')
        mid = index.get(key)
        if mid is None or row.get('match_id') != mid:
            raise ValueError('IDENTITY_MISMATCH')
        if mid in used_matches:
            raise ValueError('DUPLICATE_MATCH')
        stats = row.get('statistics')
        if not isinstance(stats, dict) or set(stats) - set(METRICS):
            raise ValueError('SCHEMA_DRIFT')
        parsed = {}
        for metric in METRICS:
            pair = stats.get(metric)
            if pair is None:
                parsed[metric] = {'home': None, 'away': None}
                continue
            if not isinstance(pair, dict) or set(pair) != {'home', 'away'}:
                raise ValueError('SCHEMA_DRIFT')
            if any(v is not None and (type(v) is not int or v < 0) for v in pair.values()):
                raise ValueError('INVALID_STATISTIC')
            parsed[metric] = dict(pair)
        used_sources.add(sid)
        used_matches.add(mid)
        result.append({'match_id': mid, 'source_match_id': sid, 'statistics': parsed})
    return result

def archive_plan(*args, **kwargs):
    raise PermissionError('BLOCKED_PERMISSION_EVIDENCE')
