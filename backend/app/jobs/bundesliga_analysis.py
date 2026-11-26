"""Read-only team performance from verified Bundesliga SQL results."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import ActiveMatch, MatchResult
from app.storage.lifecycle import aware


class AnalysisBlocked(ValueError):
    pass


def get_team_stats(
    team: str, *, days: list[date] | None = None,
    session: Session | None = None,
) -> dict[str, Any]:
    """Verified Bundesliga 2026-27 only. Never creates an SQL engine or writes."""
    cfg = get_settings()
    if cfg.operational_delete_enabled or cfg.auto_retention_delete_enabled:
        raise AnalysisBlocked("DELETE_GATES_MUST_BE_DISABLED")
    if not isinstance(team, str) or not team.strip() or len(team) > 150:
        raise AnalysisBlocked("INVALID_TEAM")
    team = team.strip()
    if days is not None and (not 1 <= len(days) <= 3 or len(set(days)) != len(days)
                             or any(not isinstance(d, date) for d in days)):
        raise AnalysisBlocked("INVALID_DAY_SCOPE")

    stats: dict[str, Any] = dict(
        team=team, played=0, wins=0, draws=0, losses=0,
        goals_for=0, goals_against=0, goal_difference=0,
        home_played=0, home_wins=0, home_goals_for=0, home_goals_against=0,
        away_played=0, away_wins=0, away_goals_for=0, away_goals_against=0,
        form=[], matches_analyzed=0, session_available=session is not None,
        warnings=[], operation="BUNDESLIGA_TEAM_STATS_READ_ONLY")
    if session is None:
        stats["warnings"].append("SQL_SESSION_UNAVAILABLE")
        return stats

    stmt = select(ActiveMatch, MatchResult).join(
        MatchResult, MatchResult.match_id == ActiveMatch.id).where(
        ActiveMatch.league_id == "de.bl1",
        ActiveMatch.season == "2026-2027",
        ActiveMatch.status == "FINISHED",
        MatchResult.status == "FINAL",
        MatchResult.verified_at.is_not(None),
        or_(ActiveMatch.home_team == team, ActiveMatch.away_team == team),
    )
    if days:
        # Apply each exact UTC day instead of including unrelated intervening days.
        day_filters = []
        for d in days:
            start = datetime.combine(d, time.min, tzinfo=timezone.utc)
            day_filters.append((ActiveMatch.start_time >= start) &
                               (ActiveMatch.start_time < start + timedelta(days=1)))
        stmt = stmt.where(or_(*day_filters))

    # Session is supplied by the API's dedicated read-only PostgreSQL dependency.
    rows = session.execute(stmt).all()
    eligible = []
    for match, result in rows:
        if (type(result.home_score) is not int or type(result.away_score) is not int
                or result.home_score < 0 or result.away_score < 0
                or match.home_team == match.away_team
                or aware(result.verified_at) < aware(match.start_time)):
            continue
        eligible.append((match, result))
    eligible.sort(key=lambda pair: aware(pair[0].start_time), reverse=True)

    recent = []
    for match, result in eligible:
        home = match.home_team == team
        gf, ga = ((result.home_score, result.away_score) if home
                  else (result.away_score, result.home_score))
        outcome = "W" if gf > ga else ("D" if gf == ga else "L")
        stats["played"] += 1
        stats[{"W": "wins", "D": "draws", "L": "losses"}[outcome]] += 1
        stats["goals_for"] += gf
        stats["goals_against"] += ga
        key = "home" if home else "away"
        stats[f"{key}_played"] += 1
        stats[f"{key}_goals_for"] += gf
        stats[f"{key}_goals_against"] += ga
        if outcome == "W":
            stats[f"{key}_wins"] += 1
        if len(recent) < 5:
            recent.append(outcome)
    stats["goal_difference"] = stats["goals_for"] - stats["goals_against"]
    stats["form"] = recent  # newest first
    stats["matches_analyzed"] = stats["played"]
    if not eligible:
        stats["warnings"].append("NO_VERIFIED_RESULTS")
    return stats
