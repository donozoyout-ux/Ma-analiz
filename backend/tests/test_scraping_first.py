"""Offline fixtures only; the checked-in single-day report holds live evidence."""
from datetime import date, datetime, timezone, timedelta
from types import SimpleNamespace
import httpx
import pytest
from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import Session
from app.core.config import Settings
from app.jobs.scraping_probe import probe
from app.providers.base import ProviderAccessRestricted, ProviderNotConfigured
from app.providers.goaloo import GoalooProvider
from app.providers.identity import fixture_key, deduplicate_scope
from app.providers.registry import create_provider, provider_health, ProviderCaller
from app.providers.source_policy import SOURCES


def fixture(provider="goaloo",league="8",**changes):
    return {"provider":provider,"provider_match_id":"test-id","league_id":league,"league_name":"Test league",
            "season":"2026-2027","home_team":"Test Home","away_team":"Test Away",
            "start_time":datetime(2026,11,20,19,tzinfo=timezone.utc),"status":"SCHEDULED",**changes}


def test_paid_provider_never_auto_selected_or_contacted():
    cfg=Settings(_env_file=None,provider_name="",match_provider="",sportmonks_api_token="test")
    assert not cfg.provider_id
    cfg.provider_name="sportmonks"
    with pytest.raises(ProviderNotConfigured,match="PAID_PROVIDER_DISABLED"): create_provider(cfg)
    assert provider_health(cfg)["status"]=="PAID_PROVIDER_DISABLED"
    from app.providers.sportmonks import SportmonksProvider
    with pytest.raises(ProviderNotConfigured): SportmonksProvider(token="test")


def test_goaloo_requires_no_token_and_csv_restricted_source_is_not_enabled():
    cfg=Settings(_env_file=None,provider_name="goaloo",provider_base_url="",provider_api_key=None)
    provider=create_provider(cfg)
    try: assert provider.name=="goaloo"
    finally:provider.close()
    assert SOURCES["football_data_co_uk"]["download_enabled"] is False


def test_robots_blocked_odds_request_is_not_sent():
    requests=[]
    def handler(request):
        requests.append(request.url.path)
        return httpx.Response(200,text="User-agent: *\nDisallow: /ajax/",request=request)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        provider=GoalooProvider(client=client)
        provider._fixture_cache["123"]={"status":"SCHEDULED"}
        with pytest.raises(ProviderAccessRestricted):provider.get_odds("123")
    assert requests==["/robots.txt"]


@pytest.mark.parametrize("status",[301,302,403,429])
def test_redirects_and_access_restrictions_are_never_followed_or_retried(status):
    requests=[]
    def handler(request):
        requests.append(request.url.path)
        if request.url.path=="/robots.txt":return httpx.Response(200,text="User-agent: *",request=request)
        return httpx.Response(status,headers={"Location":"https://unreviewed.invalid/path"},request=request)
    with httpx.Client(transport=httpx.MockTransport(handler),follow_redirects=True) as client:
        provider=GoalooProvider(client=client,requests_per_minute=600000)
        with pytest.raises(ProviderAccessRestricted):
            ProviderCaller(provider,max_retries=3,sleep=lambda _:None).call("_get","https://football.goaloo.com/data")
    assert requests==["/robots.txt","/data"]


def test_robots_request_rate_is_honoured():
    with httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(200,text="User-agent: *\nCrawl-delay: 10\nRequest-rate: 3/60",request=r))) as client:
        provider=GoalooProvider(client=client)
        provider._check_robots("https://football.goaloo.com/data")
        assert provider._request_interval>=20


def test_identity_never_guesses_club_aliases_home_away_or_kickoff():
    one=fixture()
    other=fixture(provider="openligadb",league="bl1",home_team="TEST HOME",provider_match_id="another")
    assert fixture_key(one)==fixture_key(other)
    for changed in [fixture(home_team="Test Home FC"),fixture(home_team="Test Away",away_team="Test Home"),fixture(start_time=one["start_time"]+timedelta(minutes=1))]:
        assert fixture_key(changed)!=fixture_key(one)
    assert fixture_key(fixture(league="unknown")) is None
    rows,duplicates=deduplicate_scope([one,other])
    assert len(rows)==len(duplicates)==1


def test_cross_source_duplicate_is_skipped_in_active_storage_without_migration(monkeypatch):
    from app.jobs.ingestion import upsert_matches
    from app.models import ActiveMatch
    engine=create_engine("sqlite://")
    ActiveMatch.__table__.create(engine)
    monkeypatch.setattr("app.jobs.ingestion.get_settings",lambda:SimpleNamespace(active_state_retention_days=14))
    with Session(engine) as db:
        now=datetime(2026,11,20,tzinfo=timezone.utc)
        assert upsert_matches(db,[fixture()],now=now)==(1,0)
        assert upsert_matches(db,[fixture(provider="openligadb",league="bl1",provider_match_id="other")],now=now)==(0,1)
        assert upsert_matches(db,[fixture()],now=now)==(1,0)
        assert db.scalar(select(func.count()).select_from(ActiveMatch))==1
    engine.dispose()


class ProbeFixture:
    base_url="https://football.goaloo.test"
    closed=False
    def get_matches_on_day(self,day,league,season):
        assert day==date(2026,11,20) and league=="8" and season=="2026-2027"
        return [fixture()]
    def _results(self,rows):return []
    def get_odds(self,identity):raise ProviderAccessRestricted("blocked")
    def get_statistics(self,identity):return []
    def close(self):self.closed=True


def test_probe_reports_missing_scores_odds_and_history_without_fabrication():
    provider=ProbeFixture()
    report=probe(date(2026,11,20),"8",max_matches=1,provider=provider)
    assert report["status"]=="PARTIAL" and not report["writes"] and not report["paid_fallback"]
    assert not report["live_test"] and report["match_count"]==1
    row=report["matches"][0]
    assert row["odds"]==[] and row["result"] is None
    assert row["fields"]["odds"]=="BLOCKED" and row["fields"]["score"]=="NOT_AVAILABLE"
    assert row["closing_odds"]==row["opening_odds"]=="NOT_AVAILABLE"
    assert row["analysis_input"]["home"]["form"]["sample_size"]==0
    assert report["sql"]==report["r2"]=="NOT_USED" and provider.closed


def test_scope_cannot_expand_and_istanbul_midnight_is_handled(monkeypatch):
    with pytest.raises(ValueError):probe(date(2026,11,20),"8,36",provider=ProbeFixture())
    with pytest.raises(ValueError):probe(date(2026,11,20),"8",max_matches=100,provider=ProbeFixture())
    provider=GoalooProvider(league_ids=("8",),season_ids=("2026-2027",))
    monkeypatch.setattr(provider,"_match_rows",lambda league,season:[fixture(),fixture(provider_match_id="outside",start_time=datetime(2026,11,20,22,tzinfo=timezone.utc))])
    try:
        assert [r["provider_match_id"] for r in provider.get_matches_on_day(date(2026,11,20),"8","2026-2027")]==["test-id"]
    finally:provider.close()



def test_unfinished_source_statistics_are_preserved_but_not_used_as_final_evidence():
    from app.jobs.scraping_probe import usable_statistics
    stats=[{"name":"Shots","home_value":4,"away_value":1}]
    assert usable_statistics(fixture(),stats)==[]
    assert usable_statistics(fixture(status="FINISHED"),stats)==stats
    assert usable_statistics(fixture(status="FINISHED"),[{"name":"Shots","home_value":None,"away_value":None}])==[]


def test_verified_final_results_flow_into_form_analysis():
    from app.models import ActiveMatch, MatchResult
    from app.analysis.scanning import _verified_history, _statistics_for_match
    engine=create_engine("sqlite://")
    for table in (ActiveMatch.__table__, MatchResult.__table__):table.create(engine)
    start=datetime(2026,11,20,19,tzinfo=timezone.utc)
    with Session(engine) as db:
        match=ActiveMatch(**fixture(status="FINISHED"),expires_at=start+timedelta(days=1))
        db.add(match);db.flush()
        db.add(MatchResult(provider="goaloo",match_id=match.id,home_score=2,away_score=1,
                           status="FINAL",result_source="goaloo",verified_at=start+timedelta(hours=2)))
        db.commit()
        history=_verified_history(db,cutoff=start+timedelta(hours=3))
        assert len(history)==1 and history[0]["provider"]=="goaloo"
        summary=_statistics_for_match(match,history,as_of=start+timedelta(hours=3))
        assert summary["home"]["form_5"]["sample_size"]==1
    engine.dispose()



def test_existing_broad_environment_cannot_start_a_multi_league_or_season_worker():
    for leagues,seasons in [("8,36","2026-2027"),("8","2025-2026,2026-2027")]:
        cfg=Settings(_env_file=None,provider_name="goaloo",enabled_leagues=leagues,enabled_seasons=seasons)
        with pytest.raises(ProviderNotConfigured,match="SCRAPING_SCOPE_LIMIT"):
            create_provider(cfg)
