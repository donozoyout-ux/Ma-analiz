"""Synthetic offline cases and MockTransport integration; never production evidence."""
from copy import deepcopy
from datetime import datetime, timezone
import json

import httpx
import pytest

from app.jobs.multileague_probe import main, run
from app.providers.evidence_http import EvidenceHTTP
from app.providers.multileague import bounded, compare, openfootball, openligadb

AT = datetime(2026, 11, 21, 10, tzinfo=timezone.utc)


def fixture(**changes):
    return {"matchID": 83237, "leagueId": 4937, "leagueShortcut": "bl1", "leagueSeason": 2026,
            "group": {"groupOrderID": 10}, "matchDateTimeUTC": "2026-11-20T19:30:00Z",
            "team1": {"teamName": "Test Home", "teamId": 40},
            "team2": {"teamName": "Test Away", "teamId": 65}, "matchIsFinished": True,
            "matchResults": [{"resultTypeID": 2, "pointsTeam1": 1, "pointsTeam2": 1}], **changes}


def decode(raw=None):
    return openligadb([fixture() if raw is None else raw], "de.bl1", "bl1", 4937, 10,
                       observed_at=AT, source_url="https://api.openligadb.de/getmatchdata/bl1/2026/10")


def football(**changes):
    return {"name": "English Premier League 2026/27", "matches": [{
        "date": "2026-11-21", "time": "15:00", "team1": "Test Home", "team2": "Test Away", **changes}]}


def decode_football(payload=None):
    return openfootball(football() if payload is None else payload, "en.pl", observed_at=AT,
                        source_url="https://raw.githubusercontent.com/openfootball/football.json/master/2026-27/en.1.json")


def test_source_final_is_not_production_final():
    row = decode()[0]
    assert row["source_final_confirmed"] is True
    assert row["final_confirmed"] is False
    assert row["reported_score"] == [1, 1]
    assert row["start_time"] == "2026-11-20T19:30:00+00:00"
    assert row["source_match_id"] == "83237"
    assert row["licence"] == "ODbL-1.0"


@pytest.mark.parametrize("changes", [
    {"matchIsFinished": False}, {"matchIsFinished": "true"}, {"matchIsFinished": 1},
    {"matchResults": []}, {"matchDateTimeUTC": "2026-11-20T23:30:00Z"},
    {"matchResults": [{"resultTypeID": 2, "pointsTeam1": True, "pointsTeam2": 1}]},
    {"matchResults": [{"resultTypeID": 2, "pointsTeam1": 1, "pointsTeam2": 1},
                      {"resultTypeID": 2, "pointsTeam1": 2, "pointsTeam2": 1}]},
])
def test_incomplete_or_conflicting_final_blocked(changes):
    observed = AT if "matchDateTimeUTC" not in changes else datetime(2026, 11, 20, 20, tzinfo=timezone.utc)
    rows = openligadb([fixture(**changes)], "de.bl1", "bl1", 4937, 10, observed_at=observed, source_url="test")
    assert not rows[0]["source_final_confirmed"]
    assert not rows[0]["final_confirmed"]


@pytest.mark.parametrize("changes", [
    {"leagueId": 5996}, {"leagueShortcut": "pl"}, {"leagueSeason": 2025},
    {"group": {"groupOrderID": 11}}, {"matchID": None}, {"matchID": True},
    {"team1": {"teamId": 65, "teamName": "Test Home"}},
    {"matchDateTimeUTC": "2026-11-20T20:30:00"},
])
def test_identity_and_utc_fail_closed(changes):
    with pytest.raises(ValueError):
        decode(fixture(**changes))


def test_bare_local_time_and_reported_score_never_become_final():
    row = decode_football(football(score={"ft": [2, 0]}))[0]
    assert row["league"] == "en.pl" and row["start_time"] is None
    assert row["reported_score"] == [2, 0]
    assert row["source_match_id"] is None
    assert row["identity_kind"] == "CONTENT_FINGERPRINT_NOT_SOURCE_ID"
    assert not row["source_final_confirmed"] and not row["final_confirmed"]


def test_wrong_league_file_or_inconsistent_day_is_rejected():
    with pytest.raises(ValueError):
        decode_football({**football(), "name": "Some other league"})
    with pytest.raises(ValueError):
        decode_football(football(utcDate="2026-11-22T00:00:00Z"))


def test_naive_observation_rejected():
    with pytest.raises(ValueError):
        openfootball(football(), "en.pl", observed_at=AT.replace(tzinfo=None), source_url="test")


def test_only_selected_utc_day_and_no_fake_statistics():
    assert decode(fixture(matchDateTimeUTC="2026-11-21T00:01:00Z")) == []
    rows, _ = bounded(decode(), 2)
    assert all(rows[0][k] == "NOT_AVAILABLE" for k in ("technical_statistics", "corners", "cards", "odds"))


def test_dedup_is_idempotent_and_conflict_before_truncation():
    rows = decode()
    assert bounded(rows * 2, 1) == bounded(rows, 1)
    changed = deepcopy(rows[0]); changed["reported_score"] = [9, 9]
    with pytest.raises(ValueError, match="DUPLICATE_SOURCE_ID_CONFLICT"):
        bounded(rows + [changed], 1)


def test_comparison_never_merges_and_reports_score_time_status_conflicts():
    left = bounded(decode(), 2)[0][0]
    right = {**left, "identity_scope": "other", "observation_key": "other", "start_time": "different",
             "reported_score": [0, 0], "source_status": "NOT_FINISHED"}
    result = compare([left, right])[0]
    assert result["status"] == "CONFLICT" and not result["mapping_accepted"]
    assert set(result["conflicting_fields"]) == {"start_time", "reported_score", "source_status"}
    assert compare([left, {**right, "home_team": "Test Home FC"}]) == []  # no fuzzy alias


@pytest.mark.parametrize("kwargs", [{"limit": 3}, {"leagues": []}, {"leagues": ["de.bl1", "de.bl1"]}, {"leagues": ["other"]}])
def test_invalid_scope_rejected_before_network(kwargs):
    with pytest.raises(ValueError):
        run(**kwargs)


def test_cli_cannot_persist_or_expand_date_range():
    for args in (["--persist"], ["--date", "2026-11-22"], ["--max-matches", "100"]):
        with pytest.raises(SystemExit) as exc:
            main(args)
        assert exc.value.code == 2


def test_read_only_transport_integration_and_access_stop():
    calls = []
    def handler(request):
        assert request.method == "GET" and "authorization" not in request.headers
        calls.append(str(request.url))
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        if request.url.host == "api.openligadb.de":
            return httpx.Response(200, json=[fixture()])
        if "de.1.json" in request.url.path:
            return httpx.Response(200, json={"name": "Deutsche Bundesliga 2026/27", "matches": []})
        return httpx.Response(403, text="secret-must-never-be-logged")
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        h = EvidenceHTTP(client=client, sleep=lambda _: None)
        report = run(leagues=["tr.sl", "de.bl1"], http=h)
    assert report["evidence_kind"] == "INJECTED_TEST_TRANSPORT"
    assert report["sql"] == report["r2"] == report["LIVE_DELETE"] == "NOT_RUN"
    assert not report["writes_allowed"] and not report["persistence_ready"]
    assert report["leagues"]["tr.sl"]["sources"][1]["error_code"] == "ACCESS_STOP_HTTP_403"
    assert "secret-must" not in json.dumps(report)
    assert len(calls) == 4
    assert report["leagues"]["de.bl1"]["sources"][0]["error_code"] == "ORIGIN_ACCESS_STOP"
    assert report["leagues"]["de.bl1"]["production_final_results"] == []
