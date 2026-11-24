import httpx
print('Testing Goaloo...')
try:
    resp = httpx.get('https://football.goaloo.com/jsData/leagueSeason/sea8.json', timeout=10.0)
    print(f'Status: {resp.status_code}')
    data = resp.json()
    print(f'Data type: {type(data)}')
    if isinstance(data, dict):
        print(f'Keys: {list(data.keys())}')
        if 'SeasonList' in data:
            print(f'Seasons: {data["SeasonList"]}')
except Exception as e:
    print(f'Error: {e}')