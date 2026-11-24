import httpx
from datetime import datetime

print('Testing Bundesliga match data from Goaloo...')
try:
    resp = httpx.get('https://football.goaloo.com/jsData/matchResult/json/2026-2027/s8_en.json', timeout=10.0)
    data = resp.json()
    
    schedule = data['ScheduleList']
    
    # Extract rounds - Goaloo uses nested structure
    matches = []
    for round_key, round_data in schedule.items():
        if isinstance(round_data, list):
            for match_row in round_data:
                if isinstance(match_row, list) and len(match_row) >= 8:
                    match_id = match_row[0]
                    status_code = match_row[2]
                    kickoff = match_row[3]
                    home_team_id = match_row[4]
                    away_team_id = match_row[5]
                    score = match_row[6]
                    
                    # Get team names
                    teams = {str(t[0]): t[1] for t in data['TeamInfo']}
                    home_name = teams.get(str(home_team_id), 'Unknown')
                    away_name = teams.get(str(away_team_id), 'Unknown')
                    
                    matches.append({
                        'match_id': match_id,
                        'status_code': status_code,
                        'kickoff': kickoff,
                        'home_team': home_name,
                        'away_team': away_name,
                        'score': score
                    })
    
    print(f'Total matches found: {len(matches)}')
    if matches:
        print(f'First 3 matches:')
        for m in matches[:3]:
            print(f'  {m}')
            
        finished = [m for m in matches if m['status_code'] == '-1' or m['status_code'] == '1']
        print(f'Finished matches: {len(finished)}')
        
except Exception as e:
    import traceback
    print(f'Error: {e}')
    traceback.print_exc()