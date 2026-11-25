#!/usr/bin/env python3
"""OpenLigaDB match collection test."""
import json
from datetime import datetime, date, timezone
import httpx
from collections import defaultdict

BASE_URL = "https://api.openligadb.de"
HEADERS = {"User-Agent": "MacAnaliz/1.0"}

print("=== OpenLigaDB Match Collection Test ===")
print()

# Get all Bundesliga 2026 matches
with httpx.Client(timeout=15.0) as client:
    resp = client.get(f"{BASE_URL}/getmatchdata/bl1/2026", headers=HEADERS)
    
    if resp.status_code == 200:
        all_matches = resp.json()
        print(f"Total Bundesliga 2026/27 matches: {len(all_matches)}")
        
        finished = [m for m in all_matches if m.get("matchIsFinished")]
        scheduled = [m for m in all_matches if not m.get("matchIsFinished")]
        
        print(f"Finished matches: {len(finished)}")
        print(f"Scheduled/Live matches: {len(scheduled)}")
        
        by_matchday = defaultdict(list)
        for m in all_matches:
            group_id = m.get("group", {}).get("groupOrderID", 0)
            by_matchday[group_id].append(m)
        
        print(f"Matchdays: {len(by_matchday)}")
        print()
        
        # Show today's matches in Turkey timezone
        today_utc = datetime.now(timezone.utc).date()
        print(f"Today (UTC): {today_utc}")
        
        todays = []
        for m in all_matches:
            dt_str = m.get("matchDateTimeUTC", "")
            if dt_str:
                dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
                if dt.date() == today_utc:
                    todays.append(m)
        
        print(f"Matches for today: {len(todays)}")
        
        # Show sample finished matches
        if finished:
            print()
            print("Sample finished matches:")
            for m in finished[:3]:
                dt = m.get("matchDateTimeUTC", "")[:19]
                h = m.get("team1", {}).get("teamName", "Unknown")
                a = m.get("team2", {}).get("teamName", "Unknown")
                print(f"  {dt}: {h} vs {a}")
                scores = [s for s in m.get("matchResults", []) if s.get("resultTypeID") == 2]
                if scores:
                    s = scores[0]
                    print(f"    Score: {s.get('pointsTeam1')}-{s.get('pointsTeam2')}")
        
        print()
        print("=== Collection Ready ===")
        print("Data can be collected for actual pipeline execution.")