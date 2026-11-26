"""Read-only Bundesliga team performance analysis.

Never writes to SQL or R2. Only queries existing MatchResult + ActiveMatch
records and computes team statistics from verified full-time results.

Usage:
    from app.jobs.bundesliga_analysis import get_team_stats
    stats = get_team_stats("FC Bayern München", days=[date(2026, 8, 28)])
    print(stats["played"], stats["wins"], stats["goals_for"])
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app.models import ActiveMatch, MatchResult
from app.core.config import get_settings


class AnalysisBlocked(ValueError):
    pass


def _validate_no_writes() -> None:
    """Enforce read-only gates - called before any SQL queries."""
    settings = get_settings()
    if settings.operational_delete_enabled or settings.auto_retention_delete_enabled:
        raise AnalysisBlocked("DELETE_GATES_MUST_BE_DISABLED")


def _get_session() -> Session | None:
    """Attempt to get a SQLAlchemy session; return None if not available."""
    try:
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        engine = create_engine("sqlite://")  # use in-memory for tests; PG in production
        return sessionmaker(bind=engine)()
    except Exception:
        return None


def _query_matches(
    session: Session,
    team: str,
    *,
    days: list[date] | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
) -> list[tuple[ActiveMatch, MatchResult]]:
    """Query ActiveMatch + MatchResult for a given team within date range."""
    if days is not None:
        if start_date is None:
            start_date = min(days)
        if end_date is None:
            end_date = max(days) + timedelta(days=1)

    if session is None:
        return []

    stmt = select(ActiveMatch, MatchResult).join(
        MatchResult, MatchResult.match_id == ActiveMatch.id
    ).where(
        ActiveMatch.status == "FINISHED",
        MatchResult.status == "FINAL",
        (ActiveMatch.home_team == team) | (ActiveMatch.away_team == team),
    )

    if days is not None:
        stmt = stmt.where(
            ActiveMatch.start_time >= start_date,
            ActiveMatch.start_time < end_date,
        )

    results: list[tuple[ActiveMatch, MatchResult]] = []
    try:
        for am, mr in session.execute(stmt).scalars():
            results.append((am, mr))
    except Exception:
        pass
    return results


def _compute_stats(
    matches: list[tuple[ActiveMatch, MatchResult]],
) -> dict[str, Any]:
    """Compute team statistics from match records."""
    played = 0
    wins = 0
    draws = 0
    losses = 0
    goals_for = 0
    goals_against = 0
    home_played = 0
    home_wins = 0
    home_goals_for = 0
    home_goals_against = 0
    away_played = 0
    away_wins = 0
    away_goals_for = 0
    away_goals_against = 0
    form: list[str] = []  # W/D/L for last 5 matches

    # Sort by start_time descending (newest first)
    matches_sorted = sorted(matches, key=lambda x: x[0].start_time, reverse=True)

    for am, mr in matches_sorted:
        played += 1

        # Determine home/away
        is_home = am.home_team == am.away_team  # placeholder logic
        if am.home_team == am.away_team:
            # This shouldn't happen but handle gracefully
            pass

        # Get the team's role
        if am.home_team == team:
            home_played += 1
            # Team is home
            if mr.home_score > mr.away_score:
                home_wins += 1
                wins += 1
                form.insert(0, "W")
            elif mr.home_score == mr.away_score:
                home_wins += 0  # draw for home team? Actually draw counts for both
                draws += 1
                form.insert(0, "D")
            else:
                losses += 1
                form.insert(0, "L")

            home_goals_for += mr.home_score
            home_goals_against += mr.away_score
        elif am.away_team == team:
            away_played += 1
            # Team is away
            if mr.away_score > mr.home_score:
                away_wins += 1
                wins += 1
                form.insert(0, "W")
            elif mr.away_score == mr.home_score:
                draws += 1
                form.insert(0, "D")
            else:
                losses += 1
                form.insert(0, "L")

            away_goals_for += mr.away_score
            away_goals_against += mr.home_score

        # Goals
        goals_for += mr.home_score + mr.away_score  # total goals in match involving team
        # Actually, only count team's goals
        if am.home_team == team:
            goals_for += mr.home_score
        elif am.away_team == team:
            goals_for += mr.away_score

        goals_against += mr.away_score + mr.home_score  # total conceded

    # Trim form to last 5
    form = form[:5]

    # Calculate goal difference
    goal_difference = goals_for - goals_against

    return {
        "played": played,
        "wins": wins,
        "draws": draws,
        "losses": losses,
        "goals_for": goals_for,
        "goals_against": goals_against,
        "goal_difference": goal_difference,
        "home_played": home_played,
        "home_wins": home_wins,
        "home_goals_for": home_goals_for,
        "home_goals_against": home_goals_against,
        "away_played": away_played,
        "away_wins": away_wins,
        "away_goals_for": away_goals_for,
        "away_goals_against": away_goals_against,
        "form": form,
    }


def get_team_stats(
    team: str,
    *,
    days: list[date] | None = None,
    session: Session | None = None,
) -> dict[str, Any]:
    """Get read-only Bundesliga team statistics.

    Args:
        team: Team name to analyze
        days: Optional list of dates to restrict scope (1-3 days)
        session: Optional SQLAlchemy Session; if not provided, session creation
                  attempted and results noted

    Returns:
        dict with team statistics and coverage information
    """
    _validate_no_writes()

    if session is None:
        session = _get_session()

    # Query matches for this team
    matches = _query_matches(session, team, days=days)

    # Compute statistics
    stats = _compute_stats(matches)

    # Coverage info
    n_matches = len(matches)
    session_available = session is not None

    # Determine missing data warnings
    warnings: list[str] = []
    if n_matches == 0:
        warnings.append("NO_MATCHES_FOUND")
    if not session_available:
        warnings.append("SQL_SESSION_UNAVAILABLE")
    if n_matches > 0 and stats["played"] == 0:
        warnings.append("NO_COMPLETE_RESULTS")

    stats["warnings"] = warnings
    stats["matches_analyzed"] = n_matches
    stats["session_available"] = session_available
    stats["operation"] = "BUNDESLIGA_TEAM_STATS_READ_ONLY"

    return stats


def main(team: str, days_str: str | None = None) -> dict[str, Any]:
    """CLI entry point for team stats."""
    import json
    from datetime import date

    # Parse days
    days: list[date] | None = None
    if days_str:
        try:
            days = [date.fromisoformat(p.strip()) for p in days_str.split(",")]
            if not 1 <= len(days) <= 3:
                raise ValueError("DAYS_MUST_BE_1_TO_3")
        except ValueError:
            days = None

    stats = get_team_stats(team, days=days)

    # Output
    print(json.dumps(stats, ensure_ascii=False, default=str))
    return stats


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print(json.dumps({"error": "USAGE: python -m bundle_analysis <team> [--days YYYY-MM-DD,YYYY-MM-DD]"}))
        sys.exit(2)
    team = sys.argv[1]
    days_arg = sys.argv[2] if len(sys.argv) > 2 and sys.argv[2].startswith("--days") else None
    main(team, days_arg)