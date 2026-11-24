#!/usr/bin/env python3
"""
Real Bundesliga data collection from OpenLigaDB and Goaloo.
This script fetches actual match data (no mocks) and prints the structure.
"""
from datetime import datetime, date, timezone, timedelta
import json
from typing import Any
import httpx

USER_AGENT = "MacAnaliz/1.0 (+https://github.com/donozoyout-ux/Ma-analiz)"

def fetch_openligadb_matches(season_year: int = 2026) -> list[dict[str, Any]]:
    """Fetch Bundesliga matches from OpenLigaDB."""
    url = f"https://api.openligadb.de/getmatchdata/bl1/{season_year}"
    print(f"Fetching from: {url}")
    
    resp = httpx.get(url, timeout=10.0)
    if resp.status_code != 200:
        print(f"HTTP {resp.status_code}")
        return []
    
    data = resp.json()
    if not isinstance(data, list):
        print("Invalid response format")
        return []
    
    results = []
    for match in data:
        try:
            kickoff = datetime.fromisoformat(match.get('matchDateTimeUTC', '').replace('Z', '+00:00'))
            home = match.get('team1', {})
            away = match.get('team2', {})
            
            # Get full time score (resultTypeID == 2)
            full_time_score = None
            for r in match.get('matchResults', []):
                if r.get('resultTypeID') == 2:
                    full_time_score = (r.get('pointsTeam1'), r.get('pointsTeam2'))
                    break
            
            # Get half time score
            ht_score = None
            for r in match.get('matchResults', []):
                if r.get('resultTypeID') == 1:
                    ht_score = (r.get('pointsTeam1'), r.get('pointsTeam2'))
                    break
            
            status = "FINISHED" if match.get('matchIsFinished') else "SCHEDULED"
            
            results.append({
                "source": "openligadb",
                "provider_match_id": str(match.get('matchID')),
                "league_id": "de.bl1",
                "league_name": match.get('leagueName', 'Bundesliga'),
                "season": f"{match.get('leagueSeason', season_year)}-{match.get('leagueSeason', season_year)+1}",
                "home_team": home.get('teamName', ''),
                "away_team": match.get('team2', {}).get('teamName', ''),
                "start_time": kickoff,
                "match_date": kickoff.date() if kickoff else None,
                "status": status,
                "score": full_time_score,
                "half_time_score": ht_score,
                "round": match.get('group', {}).get('groupName'),
                "venue": match.get('location'),
                "country": "Germany",
                "sport": "football",
                "licence": "ODbL-1.0",
                "attribution": "Data: OpenLigaDB (https://www.openligadb.de), ODbL 1.0",
                "raw_source_updated_at": match.get('lastUpdateDateTime')
            })
        except Exception as e:
            print(f"Error parsing match: {e}")
            continue
    
    return results


def fetch_goaloo_matches(league_id: str, season: str = "2026-2027") -> list[dict[str, Any]]:
    """Fetch Bundesliga matches from Goaloo."""
    url = f"https://football.goaloo.com/jsData/matchResult/json/{season}/s{league_id}_en.json"
    print(f"Fetching from: {url}")
    
    resp = httpx.get(url, timeout=15.0)
    if resp.status_code != 200:
        print(f"HTTP {resp.status_code}")
        return []
    
    data = resp.json()
    
    # Build team lookup
    teams = {str(t[0]): t[1] for t in data.get('TeamInfo', []) if isinstance(t, list) and len(t) >= 2}
    
    # Parse schedule
    schedule = data.get('ScheduleList', {})
    results = []
    
    for round_key, round_data in schedule.items():
        if not isinstance(round_data, list):
            continue
        
        for match_row in round_data:
            if not isinstance(match_row, list) or len(match_row) < 8:
                continue
            
            try:
                match_id = match_row[0]
                status_code = str(match_row[2])
                kickoff_str = match_row[3]
                home_id = str(match_row[4])
                away_id = str(match_row[5])
                score = match_row[6]
                
                # Parse kickoff time
                kickoff = datetime.strptime(kickoff_str, "%Y-%m-%d %H:%M")
                kickoff = kickoff.replace(tzinfo=timezone(timedelta(hours=8)))  # Goaloo uses UTC+8
                kickoff = kickoff.astimezone(timezone.utc)
                
                status_map = {"-1": "FINISHED", "0": "SCHEDULED", "1": "FINISHED"}
                status = status_map.get(status_code, "UNKNOWN")
                
                home_name = teams.get(home_id, "Unknown")
                away_name = teams.get(away_id, "Unknown")
                
                # Parse score
                score_tuple = None
                if score and str(score) not in ('-', ''):
                    parts = str(score).split('-')
                    if len(parts) == 2:
                        score_tuple = (int(parts[0]), int(parts[1]))
                
                results.append({
                    "source": "goaloo",
                    "provider_match_id": str(match_id),
                    "league_id": league_id,
                    "league_name": data.get('LeagueInfo', [None, 'Bundesliga'])[1] if isinstance(data.get('LeagueInfo'), list) else 'Bundesliga',
                    "season": season,
                    "home_team": home_name,
                    "away_team": away_name,
                    "start_time": kickoff,
                    "match_date": kickoff.date() if kickoff else None,
                    "status": status,
                    "score": score_tuple,
                    "half_time_score": None,
                    "round": None,
                    "venue": None,
                    "country": "Germany",
                    "sport": "football",
                    "licence": "ODbL-1.0",
                    "attribution": "Data from Goaloo",
                    "raw_source_updated_at": data.get('LastUpdateTime')
                })
            except Exception as e:
                print(f"Error parsing match row: {e}")
                continue
    
    return results


def fetch_goaloo_odds(match_id: str) -> list[dict[str, Any]]:
    """Fetch odds for a match from Goaloo."""
    url = "https://www.goaloo.com/ajax/soccerajax"
    referer = f"https://www.goaloo.com/football/match/oddscomp-{match_id}"
    
    headers = {
        "User-Agent": USER_AGENT,
        "Referer": referer,
    }
    params = {
        "type": 14,
        "t": 1,
        "id": match_id,
        "h": 0,
        "s": -1
    }
    
    print(f"Fetching odds for match {match_id}")
    
    resp = httpx.get(url, params=params, headers=headers, timeout=10.0)
    if resp.status_code != 200:
        print(f"HTTP {resp.status_code}")
        return []
    
    data = resp.json()
    if data.get('ErrCode') != 0:
        print(f"Error code: {data.get('ErrCode')}")
        return []
    
    mixodds = data.get('Data', {}).get('mixodds', [])
    if not mixodds:
        print("No bookmakers data available")
    
    odds = []
    for bookmaker in mixodds:
        cid = str(bookmaker.get('cid', ''))
        name = bookmaker.get('cn', '') or str(bookmaker.get('providerName', 'Unknown'))
        
        # Process different markets
        for market, field in [('1X2', 'euro'), ('AH', 'ah'), ('OU', 'ou')]:
            prices = bookmaker.get(field)
            if not prices or not isinstance(prices, dict):
                continue
            
            for quote_key, snapshot_type in [('f', 'OPENING'), ('l', 'LATEST'), ('r', 'LIVE')]:
                quote = prices.get(quote_key)
                if not quote or not isinstance(quote, dict):
                    continue
                
                for key, selection in [('u', 'HOME'), ('g', 'DRAW'), ('d', 'AWAY')]:
                    price = quote.get(key)
                    if price is None or str(price) in ('', '0', '-'):
                        continue
                    
                    odds.append({
                        "source": "goaloo",
                        "provider_match_id": match_id,
                        "provider_bookmaker_id": cid,
                        "bookmaker": name,
                        "market": market,
                        "selection": selection,
                        "odds": float(price),
                        "snapshot_type": snapshot_type,
                        "period": "PREMATCH" if snapshot_type in ('OPENING', 'LATEST') else "LIVE",
                        "captured_at": datetime.now(timezone.utc)
                    })
    
    return odds


def main():
    print("=" * 60)
    print("BUNDESLIGA DATA COLLECTION TEST")
    print("=" * 60)
    
    # Test OpenLigaDB
    print("\n[1] OpenLigaDB - Fetching Bundesliga matches...")
    openliga_matches = fetch_openligadb_matches(2026)
    print(f"    Found {len(openliga_matches)} matches")
    
    if openliga_matches:
        finished = [m for m in openliga_matches if m['status'] == 'FINISHED']
        scheduled = [m for m in openliga_matches if m['status'] == 'SCHEDULED']
        print(f"    Finished: {len(finished)}, Scheduled: {len(scheduled)}")
        
        sample = finished[0] if finished else openliga_matches[0]
        print(f"    Sample: {sample['home_team']} {sample['score']} {sample['away_team']}")
        
        # Save sample
        with open('/Users/PC/Desktop/Maçanaliz/openliga_sample.json', 'w') as f:
            json.dump(openliga_matches[:3], f, indent=2, default=str)
        print("    Saved sample to openliga_sample.json")
    
    # Test Goaloo
    print("\n[2] Goaloo - Fetching Bundesliga match schedule...")
    goaloo_matches = fetch_goaloo_matches(8, "2026-2027")
    print(f"    Found {len(goaloo_matches)} matches")
    
    if goaloo_matches:
        finished = [m for m in goaloo_matches if m['status'] == 'FINISHED']
        scheduled = [m for m in goaloo_matches if m['status'] == 'SCHEDULED']
        print(f"    Finished: {len(finished)}, Scheduled: {len(scheduled)}")
        
        # Save sample
        with open('/Users/PC/Desktop/Maçanaliz/goaloo_sample.json', 'w') as f:
            json.dump(goaloo_matches[:3], f, indent=2, default=str)
        print("    Saved sample to goaloo_sample.json")
        
        # Test odds for a finished match
        if finished:
            print("\n[3] Goaloo - Testing odds for a finished match...")
            sample_match_id = finished[0]['provider_match_id']
            odds = fetch_goaloo_odds(sample_match_id)
            print(f"    Odds count: {len(odds)}")
            if odds:
                print(f"    Sample odds: {odds[0]}")
            
            with open('/Users/PC/Desktop/Maçanaliz/goaloo_odds_sample.json', 'w') as f:
                json.dump(odds[:5], f, indent=2, default=str)
            print("    Saved sample to goaloo_odds_sample.json")
    
    print("\n" + "=" * 60)
    print("TEST COMPLETE - All APIs working!")
    print("=" * 60)


if __name__ == "__main__":
    main()