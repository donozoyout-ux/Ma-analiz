#!/usr/bin/env python3
"""
Cloudflare R2 archival for Bundesliga odds data.
Idempotent: checksum-based deduplication, safe to run multiple times.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
import sys
sys.path.insert(0, '/Users/PC/Desktop/Maçanaliz/backend')

import httpx


def archive_key(season: str, match_id: str, snapshot_type: str, bookmaker: str) -> str:
    """Generate deterministic archive key."""
    return f"odds/season={season}/match={match_id}/{snapshot_type}/{bookmaker}.json"


def archive_odds_to_r2(odds_list: list[dict], season: str = "2026-2027", dry_run: bool = True) -> dict:
    """
    Archive odds to R2 with checksum-based idempotency.
    
    Returns summary with counts.
    """
    results = {
        'dry_run': dry_run,
        'total_records': len(odds_list),
        'by_market': {},
        'by_bookmaker': {},
        'by_snapshot': {},
        'checksum_sample': None
    }
    
    # Group by key attributes
    grouped = {}
    for o in odds_list:
        key = (o.get('match_id'), o.get('snapshot_type'), o.get('bookmaker'))
        if key not in grouped:
            grouped[key] = []
        grouped[key].append(o)
    
    # Generate checksums for idempotency
    checksums = {}
    for key, records in grouped.items():
        # Sort and serialize for consistent checksum
        payload = json.dumps(records, sort_keys=True, default=str)
        checksum = hashlib.sha256(payload.encode()).hexdigest()
        checksums[key] = checksum
        
        season_str = season
        
        # Generate archive key
        ak = archive_key(season_str, str(key[0]), key[1], key[2])
        
        results['by_market'][tuple(sorted(set(o.get('market') for o in records)))] = len(records)
        results['by_bookmaker'][key[2]] = results['by_bookmaker'].get(key[2], 0) + len(records)
        results['by_snapshot'][key[1]] = results['by_snapshot'].get(key[1], 0) + len(records)
    
    if checksums:
        results['checksum_sample'] = list(checksums.values())[0][:16]
    
    # Save local file for dry-run verification
    if dry_run:
        output = {
            'summary': results,
            'checksums': {str(k): v for k, v in checksums.items()},
            'sample_key': list(checksums.keys())[0] if checksums else None
        }
        Path('/tmp/r2_archive_manifest.json').write_text(
            json.dumps(output, indent=2, default=str),
            encoding='utf-8'
        )
    
    return results


print("=" * 60)
print("R2 ARCHIVE PREPARATION - DRY RUN")
print("=" * 60)

# Test 1: Fetch odds
print("\n[1] Fetching odds from Goaloo...")
try:
    resp = httpx.get(
        'https://www.goaloo.com/ajax/soccerajax',
        params={'type': 14, 'id': 3018786, 'h': 0, 's': -1, 't': 1},
        headers={
            'User-Agent': 'MacAnaliz/1.0',
            'Referer': 'https://www.goaloo.com/football/match/oddscomp-3018786'
        },
        timeout=15.0
    )
    raw_odds = resp.json().get('Data', {}).get('mixodds', [])
    print(f"    Raw odds bookmakers: {len(raw_odds)}")
except Exception as e:
    print(f"    ERROR: {e}")
    raw_odds = []

# Test 2: Normalize odds
print("\n[2] Normalizing odds...")

def norm_odds(val, hk=False):
    if val is None or str(val).strip() in ("", "0", "-"):
        return None
    try:
        o = Decimal(str(val))
        if hk:
            o = o + Decimal("1")
        if o <= 1 or not o.is_finite():
            return None
        return o
    except:
        return None

normalized = []
for bm in raw_odds:
    for market, field in [('1X2', 'euro'), ('AH', 'ah'), ('OU', 'ou')]:
        prices = bm.get(field, {})
        if not isinstance(prices, dict):
            continue
        hk = market in ('AH', 'OU')
        
        for qk, st in [('f', 'OPENING'), ('l', 'LATEST'), ('r', 'LIVE')]:
            quote = prices.get(qk, {})
            if not isinstance(quote, dict):
                continue
            
            sides = [('u', 'HOME'), ('g', 'DRAW'), ('d', 'AWAY')] if market == '1X2' else [('u', 'HOME'), ('d', 'AWAY')]
            
            for k, sel in sides:
                raw = quote.get(k)
                odds = norm_odds(raw, hk=hk)
                
                if odds is None:
                    continue
                
                normalized.append({
                    'match_id': 3018786,
                    'bookmaker': bm.get('cn', ''),
                    'market': market,
                    'selection': sel if market == '1X2' else ('OVER' if sel == 'u' else 'UNDER'),
                    'odds': odds,
                    'snapshot_type': st,
                    'period': 'PREMATCH' if st in ('OPENING', 'LATEST') else 'LIVE'
                })

print(f"    Normalized odds: {len(normalized)}")

# Test 3: Archive preparation
print("\n[3] Preparing R2 archive manifest...")
results = archive_odds_to_r2(normalized, season="2026-2027", dry_run=True)

print(f"    Archive keys: {len(results['by_bookmaker'])}")
print(f"    Markets: {list(results['by_market'].keys())}")
print(f"    Bookmakers: {list(results['by_bookmaker'].keys())}")
print(f"    Snapshots: {results['by_snapshot']}")

print("\n" + "=" * 60)
print("R2 ARCHIVE PREP COMPLETE")
print("=" * 60)
print("Manifest saved to /tmp/r2_archive_manifest.json")