"""Counts only complete stat pairs; no source loading or network."""
from app.historical.discipline_stats import summarize

ROWS = [
 {"home_team":"Home","away_team":"Away","home_corners":0,"away_corners":10,
  "home_yellows":2,"away_yellows":1,"home_reds":None,"away_reds":None},
 {"home_team":"Away","away_team":"Home","home_corners":None,"away_corners":3,
  "home_yellows":0,"away_yellows":0,"home_reds":0,"away_reds":1},
]


def test_metric_denominators_are_not_assumed_equal():
    answer = summarize(ROWS)
    assert answer["match_count"] == 2
    assert answer["metrics"]["corners"] == {
        "sample":1, "average_total":10.0, "average_for":None, "average_against":None}
    assert answer["metrics"]["yellows"]["average_total"] == 1.5
    assert answer["metrics"]["reds"]["sample"] == 1
    assert answer["metrics"]["reds"]["average_total"] == 1


def test_team_stats_respect_home_and_away_order():
    answer = summarize(ROWS,team="home")
    assert answer["metrics"]["yellows"]["average_for"] == 1
    assert answer["metrics"]["corners"]["average_for"] == 0
    assert answer["metrics"]["corners"]["average_against"] == 10
    assert answer["metrics"]["reds"]["average_for"] == 1


def test_empty_team_sample_has_null_averages():
    answer = summarize(ROWS,team="Unknown")
    assert answer["match_count"] == 0
    assert answer["metrics"]["corners"]["sample"] == 0
    assert answer["metrics"]["corners"]["average_total"] is None
