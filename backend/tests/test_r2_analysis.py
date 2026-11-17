from datetime import datetime, timezone

from app.historical.repository import HistoricalRepository
from app.storage.r2 import InMemoryObjectStore, parquet_bytes


def _put(store, key, rows):
    store.put_object(key, parquet_bytes(rows), content_type="application/vnd.apache.parquet")


def test_r2_similarity_query_prunes_by_partition_and_joins_verified_result_and_odds():
    store = InMemoryObjectStore()
    start = datetime(2024, 8, 1, tzinfo=timezone.utc)
    end = datetime(2024, 8, 2, tzinfo=timezone.utc)
    before = datetime(2025, 1, 1, tzinfo=timezone.utc)
    suffix = "season=2024-25/league=36/date=2024-08-01.parquet"
    match = {"provider_match_id": "fx-1", "home_team": "Home", "away_team": "Away",
             "league_id": "36", "season": "2024-25", "start_time": datetime(2024, 8, 1, 18, tzinfo=timezone.utc)}
    result = {"provider_match_id": "fx-1", "home_score": 2, "away_score": 1,
              "status": "FINAL", "verified_at": datetime(2024, 8, 1, 21, tzinfo=timezone.utc)}
    odds = [
        {"provider_match_id": "fx-1", "bookmaker": "B365", "market": "1X2", "selection": selection,
         "period": "PREMATCH", "snapshot_type": kind, "odds": price, "captured_at": datetime(2024, 8, 1, 17, tzinfo=timezone.utc)}
        for selection, opening, closing in (("HOME", 2.1, 1.9), ("DRAW", 3.2, 3.3), ("AWAY", 3.5, 3.9))
        for kind, price in (("OPENING", opening), ("CLOSING", closing))
    ]
    _put(store, "matches/" + suffix, [match])
    _put(store, "results/" + suffix, [result])
    _put(store, "odds/" + suffix, odds)
    _put(store, "matches/season=other/league=36/date=2024-08-01.parquet", [{**match, "provider_match_id": "wrong"}])

    rows = HistoricalRepository(store).get_similarity_history(league="36", season="2024-25", start=start,
        end=end, before=before, bookmaker="B365")
    assert len(rows) == 1
    row = rows[0]
    assert row["home_score"] == 2 and row["verified_at"] == result["verified_at"]
    assert row["opening_home_odds"] == 2.1 and row["closing_home_odds"] == 1.9
    assert row["home_odds"] == 1.9 and row["odds_movement"] < 0
    assert len(row["historical_odds"]) == 6


def test_r2_results_verified_after_cutoff_are_not_visible():
    store = InMemoryObjectStore()
    start = datetime(2024, 8, 1, tzinfo=timezone.utc)
    key = "season=2024-25/league=36/date=2024-08-01.parquet"
    _put(store, "matches/" + key, [{"provider_match_id": "fx", "home_team": "H", "away_team": "A",
        "league_id": "36", "season": "2024-25", "start_time": datetime(2024, 8, 1, 18, tzinfo=timezone.utc)}])
    _put(store, "results/" + key, [{"provider_match_id": "fx", "home_score": 1, "away_score": 0,
        "status": "FINAL", "verified_at": datetime(2024, 8, 2, tzinfo=timezone.utc)}])
    rows = HistoricalRepository(store).get_similarity_history(league="36", season="2024-25", start=start,
        end=start, before=datetime(2024, 8, 1, 23, tzinfo=timezone.utc))
    assert rows == []


def test_r2_similarity_eliminates_duplicate_match_rows_by_provider_identity():
    store = InMemoryObjectStore()
    start = datetime(2024, 8, 1, tzinfo=timezone.utc)
    key = "season=2024-25/league=36/date=2024-08-01.parquet"
    base = {"provider_match_id": "same-fixture", "home_team": "H", "away_team": "A",
            "league_id": "36", "season": "2024-25",
            "start_time": datetime(2024, 8, 1, 18, tzinfo=timezone.utc)}
    _put(store, "matches/" + key, [base, {**base, "home_team": "duplicate export"}])
    _put(store, "results/" + key, [{"provider_match_id": "same-fixture", "home_score": 1,
        "away_score": 0, "status": "FINAL", "verified_at": datetime(2024, 8, 1, 21, tzinfo=timezone.utc)}])
    rows = HistoricalRepository(store).get_similarity_history(league="36", season="2024-25",
        start=start, end=start, before=datetime(2025, 1, 1, tzinfo=timezone.utc))
    assert len(rows) == 1
    assert rows[0]["home_team"] == "H"


def test_r2_future_odds_and_unverified_results_are_not_attached():
    store = InMemoryObjectStore()
    start = datetime(2024, 8, 1, tzinfo=timezone.utc)
    before = datetime(2024, 8, 1, 23, tzinfo=timezone.utc)
    key = "season=2024-25/league=36/date=2024-08-01.parquet"
    _put(store, "matches/" + key, [{"provider_match_id": "fx", "home_team": "H", "away_team": "A",
        "league_id": "36", "season": "2024-25", "start_time": datetime(2024, 8, 1, 18, tzinfo=timezone.utc)}])
    _put(store, "results/" + key, [{"provider_match_id": "fx", "home_score": 1, "away_score": 0,
        "status": "FINAL", "verified_at": datetime(2024, 8, 1, 21, tzinfo=timezone.utc)}])
    _put(store, "odds/" + key, [{"provider_match_id": "fx", "bookmaker": "B365", "market": "1X2",
        "selection": "HOME", "period": "PREMATCH", "snapshot_type": "LATEST", "odds": 1.7,
        "captured_at": datetime(2024, 8, 2, tzinfo=timezone.utc)}])
    rows = HistoricalRepository(store).get_similarity_history(league="36", season="2024-25",
        start=start, end=start, before=before, bookmaker="B365")
    assert len(rows) == 1
    assert "historical_odds" not in rows[0]
