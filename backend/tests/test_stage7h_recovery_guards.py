"""Offline target guards; no database, container, real credential or SDK access."""
import hashlib
import subprocess

import pytest

from recovery_support import RecoveryFailure, RecoveryHarness, constraint, target


def environment():
    return {"TEST_POSTGRES_URL":"postgresql+psycopg://stage7h_test:stage7h_local_only@127.0.0.1:5432/stage7h_ci",
        "STAGE7H_RECOVERY_ENABLED":"true","STAGE7H_POSTGRES_CONTAINER":"a"*64,
        "AUTO_RETENTION_DELETE_ENABLED":"false","OPERATIONAL_DELETE_ENABLED":"false"}


def test_guard_accepts_only_explicit_synthetic_target_without_connecting():
    url,container=target(environment())
    assert url.database=="stage7h_ci" and container=="a"*64


@pytest.mark.parametrize("patch",[
    {"STAGE7H_RECOVERY_ENABLED":"false"},{"AUTO_RETENTION_DELETE_ENABLED":"true"},
    {"OPERATIONAL_DELETE_ENABLED":"true"},{"DATABASE_URL":"PRIVATE_SECRET"},
    {"R2_ENDPOINT":"PRIVATE_SECRET"},{"PGOPTIONS":"PRIVATE_SECRET"},
    {"STAGE7H_POSTGRES_CONTAINER":"invalid; PRIVATE_SECRET"},
    {"TEST_POSTGRES_URL":"postgresql://x:PRIVATE_SECRET@external.invalid:5432/stage7h_ci"},
    {"TEST_POSTGRES_URL":"postgresql://stage7h_test:stage7h_local_only@127.0.0.1:5432/production"},
    {"TEST_POSTGRES_URL":"postgresql://other:PRIVATE_SECRET@127.0.0.1:5432/stage7h_ci"},
    {"TEST_POSTGRES_URL":"postgresql://stage7h_test:stage7h_local_only@127.0.0.1:5432/stage7h_ci?host=external.invalid"},
])
def test_unsafe_target_is_rejected_before_any_subprocess_or_connection(patch,monkeypatch,tmp_path):
    monkeypatch.setattr(subprocess,"run",lambda *a,**kw:pytest.fail("No subprocess permitted"))
    from sqlalchemy import engine
    monkeypatch.setattr(engine.Engine,"connect",lambda *a:pytest.fail("No connection permitted"))
    with pytest.raises(RecoveryFailure) as error: RecoveryHarness({**environment(),**patch},tmp_path)
    assert "PRIVATE_SECRET" not in str(error.value) and "external.invalid" not in str(error.value)


def test_unowned_database_is_never_accepted(tmp_path):
    h=RecoveryHarness(environment(),tmp_path)
    for name in ("postgres","stage7h_ci","production",f"stage7h2_{h.token}_uncreated"):
        with pytest.raises(RecoveryFailure,match="^UNOWNED_TEST_DATABASE$"): h.check_name(name)


def test_missing_file_and_checksum_failure_cannot_reach_restore(tmp_path,monkeypatch):
    h=RecoveryHarness(environment(),tmp_path)
    monkeypatch.setattr(h,"raw_restore",lambda *a,**kw:pytest.fail("No restore permitted"))
    with pytest.raises(RecoveryFailure,match="^BACKUP_MISSING$"): h.restore(tmp_path/"absent", "a"*64,"unused",None,{})
    path=tmp_path/"synthetic.dump";path.write_bytes(b"changed")
    with pytest.raises(RecoveryFailure,match="^BACKUP_CHECKSUM_MISMATCH$"): h.restore(path,hashlib.sha256(b"original").hexdigest(),"unused",None,{})


def status_check(definition):
    return {"table_name":"archive_jobs","conname":"ck_archive_jobs_valid_archive_status","contype":"c","definition":definition,"convalidated":True}


def test_equivalent_postgresql_array_cast_layouts_compare_equal():
    original="CHECK (((status)::text = ANY ((ARRAY['PENDING'::character varying, 'VERIFIED'::character varying])::text[])))"
    restored="CHECK (((status)::text = ANY (ARRAY[('PENDING'::character varying)::text, ('VERIFIED'::character varying)::text])))"
    assert constraint(status_check(original))==constraint(status_check(restored))


@pytest.mark.parametrize("alter",["status","predicate","validated"])
def test_check_normalization_does_not_hide_changed_rules(alter):
    row=status_check("CHECK (((status)::text = ANY ((ARRAY['PENDING'::character varying])::text[])))")
    other=dict(row)
    if alter=="status":other["definition"]=other["definition"].replace("PENDING","FAILED")
    if alter=="predicate":other["definition"]="("+other["definition"]+" OR true)"
    if alter=="validated":other["convalidated"]=False
    assert constraint(row)!=constraint(other)
