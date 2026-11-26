"""Explicitly read-only Bundesliga team performance endpoint."""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.match_center import read_db
from app.jobs.bundesliga_analysis import AnalysisBlocked, get_team_stats

router = APIRouter(prefix="/bundesliga", tags=["bundesliga-analysis"])


@router.get("/team-performance")
def team_performance(
    team: str = Query(min_length=1, max_length=150),
    session: Session = Depends(read_db),
):
    try:
        return get_team_stats(team, session=session)
    except AnalysisBlocked as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
