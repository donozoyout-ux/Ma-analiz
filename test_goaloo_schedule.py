import httpx
print('Testing Goaloo match schedule...')
try:
    resp = httpx.get('https://football.goaloo.com/jsData/matchResult/json/2026-2027/s8_en.json', timeout=10.0)
    print(f'Status: {resp.status_code}')
    data = resp.json()
    print(f'Data type: {type(data)}')
    if isinstance(data, dict):
        print(f'Keys: {list(data.keys())}')
        if 'LeagueInfo' in data:
            print(f'LeagueInfo: {data["LeagueInfo"]}')
        if 'ScheduleList' in data:
            schedule = data['ScheduleList']
            print(f'ScheduleList type: {type(schedule)}')
            # Count matches
            def count_matches(obj, depth=0):
                if isinstance(obj, dict):
                    return sum(count_matches(v, depth+1) for v in obj.values())
                elif isinstance(obj, list):
                    return sum(count_matches(i, depth+1) for i in obj)
                return 1
            total = count_matches(schedule)
            print(f'Total items in schedule: {total}')
except Exception as e:
    import traceback
    print(f'Error: {e}')
    traceback.print_exc()