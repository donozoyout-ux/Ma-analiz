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
            keys.extend(sorted(item for item in found
                               if item.startswith(f"{prefix}{first.isoformat()}/part=")
                               and item.endswith(".parquet")))
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
                    event_time = (row.get("available_at") or row.get("verified_at")
                                  or row.get("retrieved_at") or row.get("settled_at") or row.get("captured_at") or row.get("created_at")
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

    def get_similarity_history(self, *, league: str, season: str, start: datetime, end: datetime,
                               before: datetime, bookmaker: str | None = None) -> list[dict]:
        """Load only requested league/season/date partitions and join verified results.

        If a bookmaker is supplied, exact bookmaker odds snapshots are attached
        by provider match ID. Rows without verified outcomes are not training data.
        """
        if before.tzinfo is None or before.utcoffset() is None:
            raise ValueError("Historical cutoff must include a timezone")
        matches = self._read(self.partition_keys(dataset="matches", season=season, league=league,
            start=start, end=end), before=before)
        results = self._read(self.partition_keys(dataset="results", season=season, league=league,
            start=start, end=end), before=before)
        outcomes = {(str(row.get("provider") or ""), str(row.get("provider_match_id"))): row for row in results
                    if row.get("provider_match_id") is not None and row.get("verified_at") is not None
                    and row.get("home_score") is not None and row.get("away_score") is not None
                    and str(row.get("status", "")).upper() in {"FINAL", "FINISHED", "FT"}}
        odds_rows = self._read(self.partition_keys(dataset="odds", season=season, league=league,
            start=start, end=end), before=before) if bookmaker else []
        odds_by_match: dict[str, list[dict]] = {}
        for row in odds_rows:
            if str(row.get("bookmaker", "")) != bookmaker or str(row.get("period", "")).upper() != "PREMATCH":
                continue
            match_key = row.get("provider_match_id")
            if match_key is not None:
                odds_by_match.setdefault((str(row.get("provider") or ""), str(match_key)), []).append(row)

        # Partition retries or overlapping source exports can contain a fixture
        # more than once. Keep one stable row per provider match identity.
        matches_by_id: dict[str, dict] = {}
        for match in matches:
            identity = match.get("provider_match_id")
            if identity is not None:
                matches_by_id.setdefault((str(match.get("provider") or ""), str(identity)), match)
        technical = self._read(self.partition_keys(dataset="statistics", season=season, league=league,
            start=start, end=end), before=before)
        technical_by_match = {}
        for observation in technical:
            identity = (str(observation.get("provider") or ""), str(observation.get("provider_match_id")))
            technical_by_match.setdefault(identity, []).append(observation)
        output = []
        for match_key, match in sorted(matches_by_id.items()):
            result = outcomes.get(match_key)
            if not result:
                continue
            row = {**match, "home_score": result["home_score"], "away_score": result["away_score"],
                   "status": "FINISHED", "verified_at": result["verified_at"]}
            row["technical_statistics"] = technical_by_match.get(match_key, [])
            snapshots = odds_by_match.get(match_key, [])
            if snapshots:
                row["historical_odds"] = snapshots
                by_market: dict[tuple[str, str, str], dict[str, dict]] = {}
                for quote in snapshots:
                    key = (str(quote.get("market", "")), str(quote.get("selection", "")),
                           str(quote.get("line_key", "")))
                    by_market.setdefault(key, {})[str(quote.get("snapshot_type", "OBSERVED")).upper()] = quote
                for (market, selection, line), values in by_market.items():
                    if market == "1X2" and selection in {"HOME", "DRAW", "AWAY"}:
                        for kind in ("OPENING", "LATEST", "CLOSING"):
                            quote = values.get(kind)
                            if quote and quote.get("odds") is not None:
                                row[f"{kind.lower()}_{selection.lower()}_odds"] = float(quote["odds"])
                        preferred = values.get("CLOSING") or values.get("LATEST") or values.get("OBSERVED") or values.get("OPENING")
                        if preferred and preferred.get("odds") is not None:
                            row[f"{selection.lower()}_odds"] = float(preferred["odds"])
                    if market == "OU" and selection == "OVER" and line and "ou_line" not in row:
                        row["ou_line"] = float(line)
                opening, closing = row.get("opening_home_odds"), row.get("closing_home_odds")
                if opening and closing and opening > 1:
                    row["odds_movement"] = (closing - opening) / opening * 100
            output.append(row)
        def fixture_time(row: dict) -> datetime | None:
            value = row.get("start_time") or row.get("match_start")
            if isinstance(value, str):
                value = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
                return None
            return value

        ordered = sorted((row for row in output if fixture_time(row) is not None), key=fixture_time)
        by_team: dict[str, list[dict]] = {}
        for row in ordered:
            at = fixture_time(row)
            verified = row.get("verified_at")
            if isinstance(verified, str):
                verified = datetime.fromisoformat(verified.replace("Z", "+00:00"))
            prior_home = [item for item in by_team.get(row["home_team"], [])
                          if item.get("verified_at") and item["verified_at"] <= at]
            prior_away = [item for item in by_team.get(row["away_team"], [])
                          if item.get("verified_at") and item["verified_at"] <= at]

            def form(items: list[dict], team: str, count: int) -> float | None:
                recent = items[-count:]
                if len(recent) < 5:
                    return None
                points = 0
                for item in recent:
                    home_side = item["home_team"] == team
                    scored = item["home_score"] if home_side else item["away_score"]
                    conceded = item["away_score"] if home_side else item["home_score"]
                    points += 3 if scored > conceded else 1 if scored == conceded else 0
                return points / (3 * len(recent))

            home_only = [item for item in prior_home if item["home_team"] == row["home_team"]]
            away_only = [item for item in prior_away if item["away_team"] == row["away_team"]]
            row["home_strength"] = form(prior_home, row["home_team"], 10)
            row["away_strength"] = form(prior_away, row["away_team"], 10)
            row["home_form"] = form(home_only, row["home_team"], 5)
            row["away_form"] = form(away_only, row["away_team"], 5)
            if row["home_strength"] is not None and row["away_strength"] is not None:
                row["form"] = (row["home_strength"] + row["away_strength"]) / 2
            team_prior_by_id = {(str(item.get("provider") or ""), str(item.get("provider_match_id"))): item for item in prior_home + prior_away}
            team_prior = list(team_prior_by_id.values())
            if len(team_prior) >= 5:
                team_prior = team_prior[-20:]
                row["avg_goals"] = sum(item["home_score"] + item["away_score"] for item in team_prior) / len(team_prior)
                row["btts_rate"] = sum(item["home_score"] > 0 and item["away_score"] > 0 for item in team_prior) / len(team_prior)
            by_team.setdefault(row["home_team"], []).append(row)
            by_team.setdefault(row["away_team"], []).append(row)
        return output
