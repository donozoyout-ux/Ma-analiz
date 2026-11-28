"""Read-only destination preflight checks: no real R2 credentials or uploads."""
from types import SimpleNamespace
import pytest
from app.jobs.discipline_target_preflight import (
    DestinationBlocked, inspect_destination, verify_target_names,
)


def cfg(db="safe_ma_analiz", bucket="approved-r2"):
    return SimpleNamespace(
        database_url_valid=True, r2_configured=True,
        database_url=f"postgresql+psycopg://test:password@localhost:5432/{db}",
        r2_bucket=bucket,
    )


def test_destination_exact_names_accepted_without_connections():
    assert verify_target_names(cfg(), expected_database="safe_ma_analiz",
                               expected_bucket="approved-r2") == "safe_ma_analiz"


@pytest.mark.parametrize("dbname,bucket,expected_db,expected_bucket,code", [
    ("safe_ma_analiz", "approved-r2", "different", "approved-r2", "DATABASE_TARGET_REVIEW_REQUIRED"),
    ("safe_ma_analiz", "approved-r2", "safe_ma_analiz", "wrong-bucket", "R2_BUCKET_REVIEW_REQUIRED"),
    ("cblegacy_db", "approved-r2", "cblegacy_db", "approved-r2", "DATABASE_TARGET_REVIEW_REQUIRED"),
    ("betappv33333", "approved-r2", "betappv33333", "approved-r2", "DATABASE_TARGET_REVIEW_REQUIRED"),
])
def test_unconfirmed_or_legacy_target_fail_closed(dbname, bucket, expected_db, expected_bucket, code):
    with pytest.raises(DestinationBlocked, match=code):
        verify_target_names(cfg(dbname,bucket), expected_database=expected_db,
                            expected_bucket=expected_bucket)


def test_preflight_cannot_use_incomplete_environment():
    bad = cfg()
    bad.r2_configured = False
    with pytest.raises(DestinationBlocked, match="DATABASE_OR_R2_NOT_CONFIGURED"):
        verify_target_names(bad, expected_database="safe_ma_analiz", expected_bucket="approved-r2")


class FakeSession:
    def __init__(self, *, revision="0011_historical_discipline", missing_tables=False):
        self.revision=revision
        self.missing_tables=missing_tables
        self.read_queries=[]
    def execute(self, statement):
        sql=str(statement).strip()
        self.read_queries.append(sql)
        if "current_database()" in sql:
            return SimpleNamespace(scalar_one=lambda: "safe_ma_analiz")
        if "alembic_version" in sql:
            return SimpleNamespace(all=lambda: [(self.revision,)])
        if "to_regclass" in sql:
            return SimpleNamespace(one=lambda: (True,not self.missing_tables,True))
        if "COUNT(*)" in sql:
            return SimpleNamespace(scalar_one=lambda: 3)
        raise AssertionError(f"Unexpected SQL: {sql}")


class FakeS3:
    def __init__(self, *, fail=False):
        self.calls=[]
        self.fail=fail
    def list_objects_v2(self, **kwargs):
        self.calls.append(("LIST", kwargs))
        if self.fail:
            raise RuntimeError("connection failed including secret=do-not-print")
        return {"KeyCount": 0}
    def put_object(self, **kwargs):
        raise AssertionError("preflight MUST NOT upload objects")
    def delete_object(self, **kwargs):
        raise AssertionError("preflight MUST NOT delete objects")


def test_preflight_only_selects_sql_and_one_bounded_bucket_list():
    db=FakeSession()
    s3=FakeS3()
    status=inspect_destination(db,s3,database_name="safe_ma_analiz",bucket="approved-r2")
    assert status["status"]=="READ_ONLY_TARGET_PREFLIGHT_PASSED"
    assert status["verified_sql_archive_receipts"]==3
    assert status["real_match_data_uploaded"] is False
    assert status["r2_write_capability_tested"] is False
    assert status["write_operations_performed"]==0
    assert s3.calls==[("LIST", {"Bucket":"approved-r2","Prefix":"match-statistics/","MaxKeys":1})]
    assert db.read_queries and all(sql.upper().startswith("SELECT") for sql in db.read_queries)


def test_bad_alembic_head_blocks_before_storage_list():
    s3=FakeS3()
    with pytest.raises(DestinationBlocked,match="ALEMBIC_HEAD_MISMATCH"):
        inspect_destination(FakeSession(revision="0010_active_statistics"),s3,
                            database_name="safe_ma_analiz",bucket="approved-r2")
    assert not s3.calls


def test_missing_schema_blocks_before_storage_list():
    s3=FakeS3()
    with pytest.raises(DestinationBlocked,match="REQUIRED_SQL_TABLE_MISSING"):
        inspect_destination(FakeSession(missing_tables=True),s3,
                            database_name="safe_ma_analiz",bucket="approved-r2")
    assert not s3.calls


def test_r2_access_denial_returns_redacted_blocker_not_credentials():
    s3=FakeS3(fail=True)
    with pytest.raises(DestinationBlocked,match="R2_READ_ONLY_ACCESS_FAILED") as err:
        inspect_destination(FakeSession(),s3,database_name="safe_ma_analiz",bucket="approved-r2")
    assert "secret" not in str(err.value)
