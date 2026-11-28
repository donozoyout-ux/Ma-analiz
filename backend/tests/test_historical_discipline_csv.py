"""Synthetic-only parser tests: never download raw source data in CI."""
from datetime import date
from io import StringIO
import csv
import pytest

from app.historical.discipline_csv import parse_season, preview

COLUMNS = ["Div","Date","HomeTeam","AwayTeam","FTHG","FTAG","HC","AC","HY","AY","HR","AR","B365H"]


def fixture(rows):
    out = StringIO()
    writer = csv.DictWriter(out, fieldnames=COLUMNS)
    writer.writeheader()
    writer.writerows(rows)
    return out.getvalue().encode()


BASE = {"Div":"E0","Date":"17/08/2024","HomeTeam":"Home FC","AwayTeam":"Away FC",
        "HC":"0","AC":"7","HY":"2","AY":"","HR":"0","AR":"1","B365H":"2.50"}


def test_zero_is_not_missing_and_odds_not_extracted():
    rows = parse_season(fixture([BASE]), league="E0", season_code="2425")
    assert len(rows) == 1 and rows[0].home_corners == 0 and rows[0].away_yellows is None
    assert rows[0].home_reds == 0 and rows[0].match_date == date(2024, 8, 17)
    assert "B365" not in str(rows)


@pytest.mark.parametrize("league", ["E0", "D1", "SP1"])
@pytest.mark.parametrize("season,day", [("2425","17/08/2024"),("2526","18/08/2025"),("2627","19/08/2026")])
def test_all_leagues_and_seasons(league, season, day):
    rows = parse_season(fixture([{**BASE,"Div":league,"Date":day}]),league=league,season_code=season)
    assert len(rows) == 1


@pytest.mark.parametrize("changes", [
    {"HC":"-1"}, {"AC":"NaN"}, {"HY":"1.5"}, {"HR":"101"},
    {"Date":"2023-08-17"}, {"Div":"D1"}, {"HomeTeam":"Away FC"},
])
def test_invalid_row_fails_closed(changes):
    with pytest.raises(ValueError):
        parse_season(fixture([{**BASE,**changes}]),league="E0",season_code="2425")


def test_duplicate_identity_refused():
    with pytest.raises(ValueError,match="DUPLICATE"):
        parse_season(fixture([BASE,BASE]),league="E0",season_code="2425")


def test_all_missing_stats_are_not_zero():
    values = {**BASE,**{k:"" for k in ("HC","AC","HY","AY","HR","AR")}}
    assert parse_season(fixture([values]),league="E0",season_code="2425") == []


def test_duplicate_header_rejected():
    with pytest.raises(ValueError,match="COLUMNS"):
        parse_season(b"Div,Date,HomeTeam,AwayTeam,HC,HC,AC,HY,AY,HR,AR\n",
                     league="E0",season_code="2425")


def test_preview_includes_coverage_and_source_digest(tmp_path):
    path=tmp_path/"E0.csv"
    path.write_bytes(fixture([BASE]))
    report=preview(path,league="E0",season_code="2425")
    assert report["complete_corners"] == 1 and report["complete_yellows"] == 0
    assert report["complete_reds"] == 1 and report["odds_imported"] == 0
    assert report["independent_verification"] is False
