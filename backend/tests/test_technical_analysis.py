from datetime import datetime, timezone, timedelta
from app.analysis.technical import technical_summary


def test_technical_stats_use_only_verified_past_games_available_observations_and_latest_snapshot():
    now=datetime(2026,11,20,20,tzinfo=timezone.utc)
    stat={"name":"Shots","period":"ftstat","unit":"count","home_value":10,"away_value":4,
          "retrieved_at":now-timedelta(hours=1),"source_url":"https://source.test/match/1"}
    row={"provider":"goaloo","provider_match_id":"1","home_team":"H","away_team":"A",
         "start_time":now-timedelta(hours=4),"verified_at":now-timedelta(hours=2),"status":"FINAL",
         "technical_statistics":[{**stat,"retrieved_at":now-timedelta(hours=2),"home_value":5},stat,
                                  {**stat,"retrieved_at":now+timedelta(hours=1),"home_value":99}]}
    summary=technical_summary("H",[row],as_of=now)
    assert summary["sample_size"]==1 and summary["metrics"][0]["average"]==10
    assert summary["metrics"][0]["sample_size"]==1
    assert technical_summary("A",[row],as_of=now)["metrics"][0]["average"]==4
    assert technical_summary("H",[row],as_of=now,venue="away")["metrics"]==[]
    assert technical_summary("H",[{**row,"verified_at":None}],as_of=now)["metrics"]==[]
    assert technical_summary("H",[{**row,"technical_statistics":[]}],as_of=now)["status"]=="INSUFFICIENT"
    assert technical_summary("H",[{**row,"technical_statistics":[{**stat,"home_value":None}]}],as_of=now)["metrics"]==[]

    assert technical_summary("H",[{**row,"start_time":now+timedelta(hours=1)}],as_of=now)["metrics"]==[]
