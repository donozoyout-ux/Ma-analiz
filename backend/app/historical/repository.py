from datetime import datetime

import pyarrow.parquet as pq

from app.storage.r2 import ObjectStore


class HistoricalRepository:
    """Reads archived parquet and applies kickoff-time cutoffs before returning evidence."""
    def __init__(self, store: ObjectStore):
        self.store = store

    def _read(self, keys: list[str], *, before: datetime) -> list[dict]:
        rows: list[dict] = []
        for key in keys:
            table = pq.read_table(self._buffer(self.store.get_object(key)))
            for row in table.to_pylist():
                started = row.get("captured_at") or row.get("created_at") or row.get("start_time") or row.get("match_start")
                if started is None:
                    continue
                if isinstance(started, str):
                    started = datetime.fromisoformat(started.replace("Z", "+00:00"))
                if started.tzinfo is None or before.tzinfo is None:
                    raise ValueError("Historical timestamps and cutoff must include timezones")
                if started <= before:
                    rows.append(row)
        return rows

    @staticmethod
    def _buffer(data: bytes):
        import io
        return io.BytesIO(data)

    def get_team_matches(self, keys: list[str], *, before: datetime, team: str) -> list[dict]:
        return [r for r in self._read(keys, before=before) if team in (r.get("home_team"), r.get("away_team"))]

    def get_team_form(self, keys: list[str], *, before: datetime, team: str, limit: int = 10) -> list[dict]:
        games = self.get_team_matches(keys, before=before, team=team)
        def timestamp(row: dict) -> datetime:
            value = row.get("start_time") or row.get("match_start")
            return datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
        return sorted(games, key=timestamp, reverse=True)[:limit]

    def get_h2h(self, keys: list[str], *, before: datetime, home: str, away: str) -> list[dict]:
        return [r for r in self._read(keys, before=before) if {r.get("home_team"), r.get("away_team")} == {home, away}]

    def get_home_away_stats(self, keys: list[str], *, before: datetime, team: str) -> dict[str, int]:
        games = self.get_team_matches(keys, before=before, team=team)
        return {"home": sum(r.get("home_team") == team for r in games), "away": sum(r.get("away_team") == team for r in games)}

    def get_historical_odds(self, keys: list[str], *, before: datetime) -> list[dict]:
        return self._read(keys, before=before)

    def get_similar_matches(self, keys: list[str], *, before: datetime, league: str | None = None) -> list[dict]:
        rows = self._read(keys, before=before)
        return [r for r in rows if league is None or r.get("league_name") == league]

    def get_league_stats(self, keys: list[str], *, before: datetime, league: str) -> list[dict]:
        return [r for r in self._read(keys, before=before) if r.get("league_name") == league]

    def get_previous_predictions(self, keys: list[str], *, before: datetime) -> list[dict]:
        return self._read(keys, before=before)
