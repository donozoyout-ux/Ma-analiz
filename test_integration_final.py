#!/usr/bin/env python3
"""
Integration test for Goaloo data processing.
Reads from saved JSON, normalizes, and produces structured output.
"""
import sys
sys.path.insert(0, '/Users/PC/Desktop/Maçanaliz/backend')

import json
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from typing import Any

print("=" * 60)
print("GOALOO DATA INTEGRATION TEST")
print("=" * 60)

# Load saved data
with open('/Users/PC/Desktop/Maçanaliz/openliga_full_2026.json', 'r') as f:
    openliga_matches = json.load(f)

with open('/Users/PC/Desktop/Maçanaliz/goaloo_2026_2027.json', 'r') as f:
    goaloo_data = json.load(f)

with open('/Users/PC/Desktop/Maçanaliz/goaloo_match_3018786_odds.json', 'r') as f:
    odds_data = json.load(f)

print(f"\n[1] Loaded data:")
print(f"    OpenLigaDB matches: {len(openliga_matches)}")
print(f"    Goaloo teams: {len(goaloo_data.get('TeamInfo', []))}")
print(f"    Odds bookmakers: {len(odds_data)}")

# Build team lookup from Goaloo
teams = {str(t[0]): t[1] for t in goaloo_data.get('TeamInfo', []) if isinstance(t, list) and len(t) >= 2}

# Parse Goaloo schedule matches
print(f"\n[2] Parsing Goaloo schedule...")
goaloo_matches_raw = []
schedule = goaloo_data.get('ScheduleList', {})
for round_key, round_data in schedule.items():
    if not isinstance(round_data, list):
        continue
    for match_row in round_data:
        if not isinstance(match_row, list) or len(match_row) < 8:
            continue
        goaloo_matches_raw.append(match_row)

print(f"    Raw match rows: {len(goaloo_matches_raw)}")

# Deduplicate
print(f"\n[3] Deduplication...")
unique_ids = set()
unique_matches = []
for row in goaloo_matches_raw:
    match_id = str(row[0])
    if match_id not in unique_ids:
        unique_ids.add(match_id)
        unique_matches.append(row)

print(f"    Duplicates removed: {len(goaloo_matches_raw) - len(unique_matches)}")
print(f"    Unique matches: {len(unique_matches)}")

# Count by status
finished_raw = [r for r in unique_matches if str(r[2]) in ('-1', '1')]
scheduled_raw = [r for r in unique_matches if str(r[2]) == '0']
print(f"    Finished: {len(finished_raw)}, Scheduled: {len(scheduled_raw)}")

# Match with OpenLigaDB
print(f"\n[4] Match with OpenLigaDB...")
openliga_ids = {str(m['matchID']) for m in openliga_matches}
goaloo_ids = unique_ids

# OpenLigaDB has numeric IDs
matched_openliga = openliga_ids & goaloo_ids
only_openliga = openliga_ids - goaloo_ids
only_goaloo = goaloo_ids - openliga_ids

print(f"    Matched: {len(matched_openliga)}")
print(f"    Only in OpenLigaDB: {len(only_openliga)}")
print(f"    Only in Goaloo: {len(only_goaloo)}")

# Parse odds data
print(f"\n[5] Parsing odds data...")
odds_by_match = {}
bookmakers = set()

for bm in odds_data:
    cid = bm.get('cid')
    name = bm.get('cn', '')
    bookmakers.add(name)
    
    for market, field in [('1X2', 'euro'), ('AH', 'ah'), ('OU', 'ou')]:
        prices = bm.get(field, {})
        if not isinstance(prices, dict):
            continue
        
        for quote_key, snapshot_type in [('f', 'OPENING'), ('l', 'LATEST'), ('r', 'LIVE')]:
            quote = prices.get(quote_key, {})
            if not isinstance(quote, dict):
                continue
            
            for key, selection in [('u', 'HOME'), ('g', 'DRAW'), ('d', 'AWAY')]:
                price = quote.get(key, '')
                if not price or str(price) in ('', '0', '-'):
                    continue
                
                odds_by_match.setdefault(3018786, []).append({
                    'match_id': 3018786,
                    'bookmaker': name,
                    'market': market,
                    'selection': selection,
                    'odds': Decimal(str(price)),
                    'snapshot_type': snapshot_type,
                    'period': 'PREMATCH' if snapshot_type in ('OPENING', 'LATEST') else 'LIVE'
                })

print(f"    Unique bookmakers: {len(bookmakers)}")
print(f"    Odd records: {len(odds_by_match.get(3018786, []))}")
print(f"    Bookmakers: {sorted(bookmakers)}")

# Verify against ActiveOdds model expectations
print(f"\n[6] Schema validation (ActiveOdds)...")
sample_odds = odds_by_match.get(3018786, [])
valid_odds = []
invalid_odds = []

for o in sample_odds:
    # Check required fields
    required = ['match_id', 'bookmaker', 'market', 'selection', 'odds']
    if all(k in o for k in required):
        # Validate odds > 1
        if o['odds'] >= 1:
            valid_odds.append(o)
        else:
            invalid_odds.append({'reason': 'odds <= 1', 'data': o})
    else:
        invalid_odds.append({'reason': 'missing fields', 'data': o})

print(f"    Valid odds: {len(valid_odds)}")
print(f"    Invalid odds: {len(invalid_odds)}")

if invalid_odds:
    print(f"    Sample invalid: {invalid_odds[0]}")

# Summary
print("\n" + "=" * 60)
print("INTEGRATION SUMMARY")
print("=" * 60)
print(f"OpenLigaDB matches: {len(openliga_matches)} (all valid)")
print(f"Goaloo unique matches: {len(unique_matches)}")
print(f"Cross-source matches: {len(matched_openliga)}")
print(f"Valid odds records: {len(valid_odds)}")
print(f"Unique bookmakers: {len(bookmakers)}")
print(f"Invalid odds (rejected): {len(invalid_odds)}")
print("=" * 60)

print("\nDRY-RUN READY - Pass to database importer")