"""Isolated PostgreSQL only: never connect to a user's actual database."""
import os
from dataclasses import replace
from datetime import date

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

URL = "postgresql+psycopg://discipline_test@127.0.0.1:55472/discipline_test"
if os.environ.get("DISCIPLINE_TEST_DATABASE_URL") != URL:
    pytest.skip("explicit local isolated discipline test DB required", allow_module_level=True)

from app.historical.discipline_csv import DisciplineRow
from app.jobs.historical_discipline_import import archive_then_index, write_verified_file
from app.storage.r2 import InMemoryObjectStore
from app.models.metadata import ArchiveJob
from app.models.historical_discipline import HistoricalDisciplineMatch as H


def row():
    return DisciplineRow("a"*64,"en.pl","Premier League","2024-2025",date(2024,8,17),
                         "SYNTHETIC Home","SYNTHETIC Away",0,6,2,None,0,1)


def report(records):
    return {"source":"football-data.co.uk",
            "source_url":"https://www.football-data.co.uk/mmz4281/2425/E0.csv",
            "file_sha256":"b"*64, "league":"en.pl", "season":"2024-2025", "rows":records}


def test_persist_idempotent_immutable_and_no_expiring_match_foreign_key():
    engine=create_engine(URL)
    with Session(engine,expire_on_commit=False) as db:
        store=InMemoryObjectStore()
        inserted=archive_then_index(db,store,report=report([row()]),permission_reference="APPROVED-LOCAL-TEST-ONLY")
        assert inserted["inserted"]==1
        db.commit()
        assert archive_then_index(db,store,report=report([row()]),
                                   permission_reference="APPROVED-LOCAL-TEST-ONLY")["unchanged"]==1
        db.commit()
        saved=db.get(H,row().source_key)
        assert saved is not None and saved.home_corners==0 and saved.away_yellows is None
        assert saved.r2_archive_key.startswith("match-statistics/season=2024-2025/league=en.pl/")
        assert len(store.list_objects())==1
        assert db.scalar(select(ArchiveJob).where(ArchiveJob.r2_key==saved.r2_archive_key)).status=="VERIFIED"
        assert not any(fk.target_fullname.startswith("active_matches")
                       for fk in H.__table__.foreign_keys)
        with pytest.raises(ValueError,match="SOURCE_CONFLICT"):
            archive_then_index(db,store,report=report([replace(row(),home_corners=2)]),
                                permission_reference="APPROVED-LOCAL-TEST-ONLY")
        db.rollback()
        with pytest.raises(DBAPIError):
            db.execute(text("UPDATE historical_discipline_matches SET home_corners=9 WHERE source_key=:k"),
                       {"k":row().source_key})
            db.commit()
        db.rollback()
        assert db.get(H,row().source_key).home_corners==0
        with pytest.raises(DBAPIError):
            db.execute(text("DELETE FROM historical_discipline_matches WHERE source_key=:k"),
                       {"k":row().source_key})
            db.commit()
        db.rollback()
    engine.dispose()


def test_read_only_summary_and_match_row_contract():
    from app.api.historical_discipline import summary, matches
    engine=create_engine(URL)
    with Session(engine) as db:
        total=summary(league="en.pl", season="2024-2025", team=None, db=db)
        assert total["match_count"]>=1
        assert total["metrics"]["corners"]["sample"]>=1
        assert total["verified_match_outcomes"] is False and total["odds_included"] is False
        found=matches(league="en.pl", season="2024-2025", team="SYNTHETIC Home",
                      limit=20, offset=0, db=db)
        assert any(row["home_corners"]==0 and row["away_yellows"] is None for row in found["items"])
        assert found["official_result_verified"] is False
    engine.dispose()


def test_as_of_before_import_cannot_see_historical_data():
    from app.api.historical_discipline import filtered_query
    from datetime import datetime, timezone
    engine=create_engine(URL)
    with Session(engine) as db:
        query=filtered_query(league="en.pl",season="2024-2025",team=None,
                             as_of=datetime(2025,1,1,tzinfo=timezone.utc))
        assert db.scalars(query).all()==[]
    engine.dispose()


def test_unverified_r2_archive_rows_are_excluded_from_public_api():
    from app.api.historical_discipline import summary
    from sqlalchemy import update
    engine=create_engine(URL)
    with Session(engine) as db:
        key=db.get(H,row().source_key).r2_archive_key
        db.execute(update(ArchiveJob).where(ArchiveJob.r2_key==key).values(status="FAILED"))
        db.flush()
        result=summary(league="en.pl",season="2024-2025",team=None,db=db)
        assert result["match_count"]==0
        assert result["archive_integrity"]=="NO_VERIFIED_ARCHIVE_ROWS"
        db.rollback()
    engine.dispose()


def test_csv_rows_refused_without_verified_r2_receipt():
    from app.storage.archive import ArchiveReceipt
    engine=create_engine(URL)
    with Session(engine) as db:
        bogus=ArchiveReceipt("match-statistics/bogus.parquet", 1, 10, "c"*64)
        with pytest.raises(ValueError,match="VERIFIED_R2_RECEIPT_REQUIRED"):
            write_verified_file(db,report=report([row()]),permission_reference="APPROVED-LOCAL-TEST-ONLY",receipt=bogus)
        db.rollback()
    engine.dispose()
