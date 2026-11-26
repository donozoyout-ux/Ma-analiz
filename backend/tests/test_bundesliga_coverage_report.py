"""Deterministic no-network/no-production-write coverage-report tests."""
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from app.jobs.bundesliga_coverage_report import generate_coverage_report, CoverageReportException
from app.models import ActiveMatch, MatchResult

DAY = date(2026, 8, 28)
NOW = date(2026, 11, 26)
KICKOFF = datetime(2026, 8, 28, 18, 30, tzinfo=timezone.utc)


def source_records(day, matchday, now):
    shared = dict(league="de.bl1", home_team="FC Bayern München",
                  away_team="VfB Stuttgart", start_time=KICKOFF.isoformat(),
                  source_status="FINISHED", reported_score=[5, 1],
                  final_confirmed=True, observed_at=now.isoformat())
    return ([{**shared, "provider": "openligadb", "source_match_id": "83156"}],
            [{**shared, "provider": "football-data.org", "source_match_id": "565776"}])


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    ActiveMatch.__table__.create(engine)
    MatchResult.__table__.create(engine)
    with Session(engine, expire_on_commit=False) as s:
        match = ActiveMatch(id=uuid4(), provider="openligadb",
                            provider_match_id="83156", league_id="de.bl1",
                            season="2026-2027", league_name="Bundesliga",
                            home_team="FC Bayern München", away_team="VfB Stuttgart",
                            start_time=KICKOFF, status="FINISHED",
                            expires_at=KICKOFF+timedelta(hours=32))
        s.add(match)
        s.flush()
        result = MatchResult(match_id=match.id, provider="openligadb",
                             home_score=5, away_score=1, status="FINAL",
                             result_source="OpenLigaDB", verified_at=KICKOFF)
        s.add(result)
        s.commit()
        yield s, match, result
    engine.dispose()


def run(*, session=None, collector=source_records, archive_checker=None, days=None):
    return generate_coverage_report(days=days or [DAY], matchday=1, today=NOW,
                                    session=session, collector=collector,
                                    archive_checker=archive_checker)


def test_distinct_provider_ids_corrob_to_one_match(db):
    s, _, _ = db
    out = run(session=s)
    assert out["fetched"] == {"openligadb": 1, "football_data_org": 1}
    assert out["matched_both_sources"] == 1
    assert len(out["candidates_for_analysis"]) == 1
    assert out["missing_from_sql"] == 0


def test_no_sql_session_is_unknown_not_missing():
    out = run()
    assert out["readiness"] == "UNKNOWN"
    assert out["missing_from_sql"] == 0
    assert out["postgresql"]["days"][DAY.isoformat()] is None


def test_no_implicit_r2_verification(db):
    s, _, _ = db
    out = run(session=s)
    assert out["verified_r2_archives"] == 0
    assert out["r2_archive"][DAY.isoformat()]["r2_status"] == "NOT_CHECKED"
    assert out["readiness"] != "READY"


def test_trusted_archive_receipt_ready(db):
    s, _, _ = db
    out = run(session=s, archive_checker=lambda match, result: True)
    assert out["readiness"] == "READY"
    assert out["verified_r2_archives"] == 1


def test_missing_sql_counts_unique_fixture(db):
    s, m, r = db
    s.delete(r)
    s.delete(m)
    s.commit()
    out = run(session=s)
    assert out["missing_from_sql"] == 1
    assert out["held"][0]["reason"] == "SQL_MATCH_NOT_FOUND"


def test_score_conflict_is_held(db):
    s, m, r = db
    r.home_score = 4
    s.commit()
    out = run(session=s)
    assert out["candidates_for_analysis"] == []
    assert out["held"][0]["reason"] == "SQL_FINAL_OR_IDENTITY_CONFLICT"


def test_no_source_match_is_not_ready(db):
    s, _, _ = db
    out = run(session=s, collector=lambda *a, **kw: ([], []))
    assert out["readiness"] == "NOT_READY"
    assert out["matched_both_sources"] == 0


def test_fetch_error_sanitized_and_no_writes(db):
    s, _, _ = db
    def broken(*a, **kw):
        raise RuntimeError("SENSITIVE_TOKEN_SHOULD_NOT_LEAK")
    out = run(session=s, collector=broken)
    assert out["held"][0]["reason"] == "SOURCE_FETCH_OR_PARSE_FAILED"
    assert "SENSITIVE_TOKEN" not in str(out)
    assert out["sql_writes"] == out["r2_writes"] == 0
    assert out["sql_delete"] is False


def test_three_day_bound(db):
    s, _, _ = db
    out = run(session=s, days=[DAY, date(2026, 8, 29), date(2026, 8, 30)])
    assert len(out["days"]) == 3
    assert out["api_requests_max"] == 6


@pytest.mark.parametrize("days", [[DAY]*2, [date(2026, 1, 1)],
                                  [date(2026, 8, 28)]*4])
def test_invalid_date_scopes(days):
    with pytest.raises(CoverageReportException):
        run(days=days)
