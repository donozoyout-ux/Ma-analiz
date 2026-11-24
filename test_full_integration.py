#!/usr/bin/env python3
"""Test script to verify real API connectivity and fetch Bundesliga match data."""
from datetime import datetime, date, timezone
import json
import httpx

print("=" * 60)
print("BUNDESLIGA DATA COLLECTION TEST")
print("=" * 60)

# Test 1: OpenLigaDB Health Check
print("\n[1] Testing OpenLigaDB API...")
try:
    resp = httpx.get('https://api.openligadb.de/getcurrentgroup/bl1', timeout=10.0)
    if resp.status_code == 200:
        data = resp.json()
        print(f"    Status: OK")
        print(f"    Current Group: {data.get('groupName', 'N/A')}")
        print(f"    Group Order ID: {data.get('groupOrderID', 'N/A')}")
    else:
        print(f"    Status: FAILED (HTTP {resp.status_code})")
except Exception as e:
    print(f"    Status: ERROR - {e}")

# Test 2: OpenLigaDB Match Data
print("\n[2] Fetching Bundesliga 2026 matches from OpenLigaDB...")
try:
    resp = httpx.get('https://api.openligadb.de/getmatchdata/bl1/2026', timeout=10.0)
    if resp.status_code == 200:
        data = resp.json()
        matches = data if isinstance(data, list) else []
        print(f"    Status: OK")
        print(f"    Total matches: {len(matches)}")
        
        if matches:
            # Show sample match structure
            sample = matches[0]
            print(f"    Sample match ID: {sample.get('matchID')}")
            print(f"    Home team: {sample.get('team1', {}).get('teamName', 'N/A')}")
            print(f"    Away team: {sample.get('team2', {}).get('teamName', 'N/A')}")
            print(f"    Kickoff (UTC): {sample.get('matchDateTimeUTC', 'N/A')}")
            print(f"    Finished: {sample.get('matchIsFinished', 'N/A')}")
            
            # Count finished matches
            finished = [m for m in matches if m.get('matchIsFinished')]
            print(f"    Finished matches: {len(finished)}")
    else:
        print(f"    Status: FAILED (HTTP {resp.status_code})")
except Exception as e:
    print(f"    Status: ERROR - {e}")

# Test 3: Goaloo League Seasons
print("\n[3] Testing Goaloo Bundesliga (ID: 8) seasons...")
try:
    resp = httpx.get('https://football.goaloo.com/jsData/leagueSeason/sea8.json', timeout=10.0)
    if resp.status_code == 200:
        data = resp.json()
        seasons = data.get('SeasonList', [])
        print(f"    Status: OK")
        print(f"    Available seasons: {len(seasons)}")
        print(f"    Current season: {seasons[0] if seasons else 'N/A'}")
    else:
        print(f"    Status: FAILED (HTTP {resp.status_code})")
except Exception as e:
    print(f"    Status: ERROR - {e}")

# Test 4: Goaloo Match Schedule
print("\n[4] Fetching Bundesliga 2026-27 matches from Goaloo...")
try:
    resp = httpx.get('https://football.goaloo.com/jsData/matchResult/json/2026-2027/s8_en.json', timeout=10.0)
    if resp.status_code == 200:
        data = resp.json()
        print(f"    Status: OK")
        
        league_info = data.get('LeagueInfo', [])
        print(f"    League: {league_info[1] if len(league_info) > 1 else 'N/A'}")
        print(f"    Season: {league_info[2] if len(league_info) > 2 else 'N/A'}")
        
        # Count matches
        schedule = data.get('ScheduleList', {})
        team_info = data.get('TeamInfo', [])
        
        # Parse matches from nested rounds
        matches = []
        for round_key, round_data in schedule.items():
            if isinstance(round_data, list):
                for match_row in round_data:
                    if isinstance(match_row, list) and len(match_row) >= 8:
                        matches.append({
                            'match_id': match_row[0],
                            'status_code': match_row[2],
                            'kickoff': match_row[3],
                            'home_team_id': match_row[4],
                            'away_team_id': match_row[5],
                            'score': match_row[6]
                        })
        
        print(f"    Total matches found: {len(matches)}")
        if matches:
            # Resolve team names
            teams = {str(t[0]): t[1] for t in team_info if isinstance(t, list) and len(t) >= 2}
            
            # Count by status
            finished = [m for m in matches if str(m['status_code']) in ('-1', '1')]
            scheduled = [m for m in matches if str(m['status_code']) == '0']
            
            print(f"    Finished matches: {len(finished)}")
            print(f"    Scheduled matches: {len(scheduled)}")
            
            # Show sample
            if finished:
                sample = finished[0]
                home = teams.get(str(sample['home_team_id']), 'Unknown')
                away = teams.get(str(sample['away_team_id']), 'Unknown')
                print(f"    Sample finished: {home} {sample['score']} {away}")
        
        # Test odds endpoint for a finished match
        if finished:
            sample_match_id = finished[0]['match_id']
            print(f"\n[5] Testing Goaloo odds for match {sample_match_id}...")
            try:
                odds_resp = httpx.get(
                    'https://www.goaloo.com/ajax/soccerajax',
                    params={'type': 14, 'id': sample_match_id, 'h': 0, 's': -1},
                    headers={
                        'User-Agent': 'MacAnaliz/1.0',
                        'Referer': f'https://www.goaloo.com/football/match/oddscomp-{sample_match_id}'
                    },
                    timeout=10.0
                )
                if odds_resp.status_code == 200:
                    odds_data = odds_resp.json()
                    print(f"    Odds status: OK")
                    if odds_data.get('ErrCode') == 0:
                        print(f"    Odds data available: Yes")
                        mixodds = odds_data.get('Data', {}).get('mixodds', [])
                        print(f"    Bookmakers with odds: {len(mixodds)}")
                    else:
                        print(f"    Odds error code: {odds_data.get('ErrCode')}")
                else:
                    print(f"    Odds status: HTTP {odds_resp.status_code}")
            except Exception as oe:
                print(f"    Odds status: ERROR - {oe}")
    else:
        print(f"    Status: FAILED (HTTP {resp.status_code})")
except Exception as e:
    print(f"    Status: ERROR - {e}")

print("\n" + "=" * 60)
print("TEST COMPLETE")
print("=" * 60)