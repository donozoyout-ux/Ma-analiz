from datetime import datetime, timedelta

import pyarrow.parquet as pq

from app.storage.r2 import ObjectStore


class HistoricalRepository:
    """Stream selected R2 partitions in batches and exclude evidence after analysis time."""
    def __init__(self, store: ObjectStore, *, batch_size: int = 4096):
        if batch_size < 1:
            raise ValueError("batch_size must be positive")
        self.store = store
        self.batch_size = batch_size

    def partition_keys(self, *, dataset: str, season: str, league: str,
                       start: datetime, end: datetime) -> list[str]:
        if start.tzinfo is None or end.tzinfo is None or end < start:
            raise ValueError("Timezone-aware ordered date bounds are required")
        prefix = f"{dataset}/season={season}/league={league}/date="
        first, last = start.date(), end.date()
        found = {obj.key for obj in self.store.list_objects(prefix)}
        keys = []
        while first <= last:
            key = f"{prefix}{first.isoformat()}.parquet"
            if key in found:
                keys.append(key)
            first += timedelta(days=1)
        return keys

    def _read(self, keys: list[str], *, before: datetime) -> list[dict]:
        if before.tzinfo is None or before.utcoffset() is None:
            raise ValueError("Historical cutoff must include a timezone")
        rows: list[dict] = []
        for key in keys:
            parquet_file = pq.ParquetFile(self._buffer(self.store.get_object(key)))
            for batch in parquet_file.iter_batches(batch_size=self.batch_size):
                for row in batch.to_pylist():
                    # A finalized result is not available to analysis until it has
                    # been verified; prefer that availability timestamp to match time.
                    event_time = (row.get("captured_at") or row.get("settled_at")
                                  or row.get("verified_at") or row.get("created_at")
                                  or row.get("finished_at") or row.get("start_time")
                                  or row.get("match_start"))
                    if event_time is None:
                        continue
                    if isinstance(event_time, str):
                        event_time = datetime.fromisoformat(event_time.replace("Z", "+00:00"))
                    if event_time.tzinfo is None or event_time.utcoffset() is None:
                        raise ValueError("Historical timestamps must include a timezone")
                    if event_time <= before:
                        rows.append(row)
        return rows

    @staticmethod
    def _buffer(data: bytes):
        import io
        return io.BytesIO(data)

    def get_team_matches(self, keys: list[str], *, before: datetime, team: str) -> list[dict]:
        return [row for row in self._read(keys, before=before) if team in (row.get("home_team"), row.get("away_team"))]

    def get_team_form(self, keys: list[str], *, before: datetime, team: str, limit: int = 10) -> list[dict]:
        games = self.get_team_matches(keys, before=before, team=team)
        def timestamp(row: dict) -> datetime:
            value = row.get("start_time") or row.get("match_start")
            return datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
        return sorted(games, key=timestamp, reverse=True)[:limit]

    def get_h2h(self, keys: list[str], *, before: datetime, home: str, away: str) -> list[dict]:
        return [row for row in self._read(keys, before=before)
                if {row.get("home_team"), row.get("away_team")} == {home, away}]

    def get_home_away_stats(self, keys: list[str], *, before: datetime, team: str) -> dict[str, int]:
        games = self.get_team_matches(keys, before=before, team=team)
        return {"home": sum(row.get("home_team") == team for row in games),
                "away": sum(row.get("away_team") == team for row in games)}

    def get_historical_odds(self, keys: list[str], *, before: datetime) -> list[dict]:
        return self._read(keys, before=before)

    def get_similar_matches(self, keys: list[str], *, before: datetime, league: str | None = None) -> list[dict]:
        rows = self._read(keys, before=before)
        return [row for row in rows if league is None or row.get("league_name") == league]

    def get_league_stats(self, keys: list[str], *, before: datetime, league: str) -> list[dict]:
        return [row for row in self._read(keys, before=before) if row.get("league_name") == league]

    def get_previous_predictions(self, keys: list[str], *, before: datetime) -> list[dict]:
        return self._read(keys, before=before)
