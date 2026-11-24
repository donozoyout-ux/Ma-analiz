#!/usr/bin/env python3
"""
PostgreSQL importer for matched Goaloo-OpenLigaDB Bundesliga data.
Idempotent: safe to run multiple times.
"""
import json
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
import sys
sys.path.insert(0, '/Users/PC/Desktop/Maçanaliz/backend')

import httpx
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session

print("=" * 60)
print("BUNDESLIGA POSTGRESQL IMPORTER - DRY RUN")
print("=" * 60)

# Test 1: Fetch source data
print("\n[1] Fetching live data from providers...")

try:
    resp_ol = httpx.get('https://api.openligadb.de/getmatchdata/bl1/2026', timeout=15.0)
    openliga_matches = resp_ol.json() if resp_ol.status_code == 200 else []
    print(f"    OpenLigaDB: {len(openliga_matches)} matches")
except Exception as e:
    print(f"    OpenLigaDB ERROR: {e}")
    openliga_matches = []

try:
    resp_go = httpx.get('https://football.goaloo.com/jsData/matchResult/json/2026-2027/s8_en.json', timeout=15.0)
    goaloo_data = resp_go.json() if resp_go.status_code == 200 else {}
    print(f"    Goaloo: {goaloo_data.get('LeagueInfo', [None, 'Bundesliga'])[1] if isinstance(goaloo_data.get('LeagueInfo'), list) else 'N/A'}")
except Exception as e:
    print(f"    Goaloo ERROR: {e}")
    goaloo_data = {}

# Test 2: Load local test data
print("\n[2] Loading local test data...")
try:
    with open('openliga_full_2026.json') as f:
        openliga_matches = json.load(f)
    with open('goaloo_2026_2027.json') as f:
        goaloo_data = json.load(f)
    with open('goaloo_match_3018786_odds.json') as f:
        odds_data = json.load(f)
    print(f"    Loaded from files")
except Exception as e:
    print(f"    File load ERROR: {e}")
    odds_data = []

# Test 3: Match counting
print("\n[3] Counting matches...")

# Build openliga index
import re
def norm(name):
    return re.sub(r'[^\w\s]', '', name).lower()

ol_index = {}
for m in openliga_matches:
    h = norm(m.get('team1', {}).get('teamName', ''))
    a = norm(m.get('team2', {}).get('teamName', ''))
    dt = m.get('matchDateTimeUTC', '')[:10]
    ol_index[(h, a, dt)] = str(m.get('matchID'))

# Build goaloo lookup
go_teams = {str(t[0]): t[1].lower() for t in goaloo_data.get('TeamInfo', []) if isinstance(t, list) and len(t) >= 2}

schedule = goaloo_data.get('ScheduleList', {})
all_goaloo = []
for rd in schedule.values():
    if isinstance(rd, list):
        all_goaloo.extend(rd)

matched = []
needs_review = []
no_match = []

for row in all_goaloo:
    if not isinstance(row, list) or len(row) < 8:
        continue
    
    gid = str(row[0])
    kickoff = str(row[3])
    
    try:
        kt = datetime.strptime(kickoff, "%Y-%m-%d %H:%M")
        kutc = kt.replace(tzinfo=timezone(timedelta(hours=8))).astimezone(timezone.utc)
        date_str = kutc.strftime("%Y-%m-%d")
    except:
        date_str = kickoff[:10]
    
    home = norm(go_teams.get(str(row[4]), ''))
    away = norm(go_teams.get(str(row[5]), ''))
    
    key = (home, away, date_str)
    
    if key in ol_index:
        matched.append({'goaloo': gid, 'openligadb': ol_index[key]})
    else:
        no_match.append({'goaloo': gid})

print(f"    Matched: {len(matched)}")
print(f"    No match: {len(no_match)}")

# Test 4: Odds normalization
print("\n[4] Normalizing odds...")

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

valid_odds = []
rejected = []

for bm in odds_data:
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
                    if raw is not None and str(raw).strip() not in ("", "0", "-"):
                        rejected.append({'market': market, 'raw': raw, 'reason': 'odds <= 1'})
                    continue
                
                valid_odds.append({
                    'match_id': 3018786,
                    'bookmaker': bm.get('cn', ''),
                    'market': market,
                    'selection': sel if market == '1X2' else ('OVER' if sel == 'u' else 'UNDER'),
                    'odds': odds,
                    'snapshot_type': st
                })

print(f"    Valid odds: {len(valid_odds)}")
print(f"    Rejected: {len(rejected)}")

# Test 5: Build import data
print("\n[5] Building import data structure...")

import_matches_by_matchid = {}
for m in openliga_matches:
    mid = m.get('matchID')
    if mid:
        kickoff = datetime.fromisoformat(m.get('matchDateTimeUTC', '').replace('Z', '+00:00'))
        ht = m.get('team1', {}).get('teamName', '')
        at = m.get('team2', {}).get('teamName', '')
        score = None
        for r in m.get('matchResults', []):
            if r.get('resultTypeID') == 2:
                score = (r.get('pointsTeam1'), r.get('pointsTeam2'))
                break
        
        # Find matching odds
        match_odds = [o for o in valid_odds if o['match_id'] == int(mid)]
        
        import_matches_by_matchid[mid] = {
            'provider': 'openligadb',
            'provider_match_id': str(mid),
            'league_id': 'de.bl1',
            'home_team': ht,
            'away_team': at,
            'start_time': kickoff,
            'status': 'FINISHED' if m.get('matchIsFinished') else 'SCHEDULED',
            'score': score,
            'odds': match_odds
        }

print(f"    Matches with odds: {sum(1 for v in import_matches_by_matchid.values() if v['odds'])}")

# Save report
report = {
    'dry_run': True,
    'openliga_matches': len(openliga_matches),
    'matched': len(matched),
    'valid_odds': len(valid_odds),
    'matches_with_odds': sum(1 for v in import_matches_by_matchid.values() if v['odds']),
    'bookmakers': list(set(o['bookmaker'] for o in valid_odds)),
    'import_data_file': '/tmp/bundesliga_import.json'
}

Path('/tmp/bundesliga_import.json').write_text(json.dumps(import_matches_by_matchid, indent=2, default=str), encoding='utf-8')
Path('/tmp/bundesliga_report.json').write_text(json.dumps(report, indent=2, default=str), encoding='utf-8')

print("\n" + "=" * 60)
print("DRY RUN COMPLETE")
print("=" * 60)
print(f"Report saved to /tmp/bundesliga_report.json")
print(f"Import data saved to /tmp/bundesliga_import.json")