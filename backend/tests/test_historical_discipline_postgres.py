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
from app.jobs.historical_discipline_import import write_verified_file
from app.models.historical_discipline import HistoricalDisciplineMatch as H


def row():
    return DisciplineRow("a"*64,"en.pl","Premier League","2024-2025",date(2024,8,17),
                         "SYNTHETIC Home","SYNTHETIC Away",0,6,2,None,0,1)


def report(records):
    return {"source":"football-data.co.uk",
            "source_url":"https://www.football-data.co.uk/mmz4281/2425/E0.csv",
            "file_sha256":"b"*64,"rows":records}


def test_persist_idempotent_immutable_and_no_expiring_match_foreign_key():
    engine=create_engine(URL)
    with Session(engine,expire_on_commit=False) as db:
        inserted=write_verified_file(db,report=report([row()]),permission_reference="APPROVED-LOCAL-TEST-ONLY")
        assert inserted["inserted"]==1
        db.commit()
        assert write_verified_file(db,report=report([row()]),
                                   permission_reference="APPROVED-LOCAL-TEST-ONLY")["unchanged"]==1
        db.commit()
        saved=db.get(H,row().source_key)
        assert saved is not None and saved.home_corners==0 and saved.away_yellows is None
        assert not any(fk.target_fullname.startswith("active_matches")
                       for fk in H.__table__.foreign_keys)
        with pytest.raises(ValueError,match="SOURCE_CONFLICT"):
            write_verified_file(db,report=report([replace(row(),home_corners=2)]),
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
