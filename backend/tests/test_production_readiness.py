from types import SimpleNamespace

from sqlalchemy import create_engine, text

from app.jobs import production_readiness as ops


def _passing_report():
    return {
        "postgres": "PASS", "alembic": "PASS", "r2": "PASS", "goaloo": "PASS",
        "postgres_details": {}, "alembic_details": {}, "r2_details": {},
        "goaloo_details": {}, "internal_health": {},
    }


def _settings(**overrides):
    values = {
        "database_url": "postgresql+psycopg://user:password@ma-db.internal:5432/macanaliz",
        "database_url_valid": True,
        "enabled_league_ids": ("36",),
        "enabled_season_ids": ("2021-2022",),
        "provider_id": "goaloo",
        "provider_requests_per_minute": 100000,
        "provider_retry_count": 1,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_missing_database_url_blocks_gate():
    settings = _settings(database_url=None, database_url_valid=False)
    pg = ops._check_postgres(settings, object())
    assert pg["configured"] == "NOT_CONFIGURED"
    report = {"postgres": "FAIL", "alembic": "FAIL", "r2": "PASS", "goaloo": "PASS"}
    result, code = ops.execute(run_backfill=True, readiness=lambda: report,
                               backfill=lambda _args: (_ for _ in ()).throw(AssertionError("must not run")))
    assert code != 0
    assert result["backfill"] == "BLOCKED"


def test_missing_r2_blocks_gate():
    settings = _settings(r2_endpoint=None)
    result = ops.run_readiness(
        settings=settings, db_engine=None,
        postgres_check=lambda *_: {"connection": "PASS", "identity": "PASS", "version": "PASS"},
        alembic_check=lambda *_: {"status": "PASS"},
        r2_runner=lambda **_: {"status": "NOT_CONFIGURED"},
        goaloo_check=lambda *_: {"status": "PASS"},
        health_check=lambda: {},
    )
    assert result["r2"] == "FAIL"
    assert not ops._gate_passed(result)


def test_goaloo_failure_blocks_gate():
    report = _passing_report()
    report["goaloo"] = "FAIL"
    result, code = ops.execute(run_backfill=True, readiness=lambda: report,
                               backfill=lambda _args: (_ for _ in ()).throw(AssertionError("must not run")))
    assert code != 0
    assert result["backfill"] == "BLOCKED"


def test_migration_mismatch_blocks_gate_without_migration():
    report = _passing_report()
    report["alembic"] = "FAIL"
    report["alembic_details"] = {"status": "NEEDS_MIGRATION"}
    result, code = ops.execute(run_backfill=True, readiness=lambda: report,
                               backfill=lambda _args: (_ for _ in ()).throw(AssertionError("must not run")))
    assert code != 0
    assert result["backfill"] == "BLOCKED"


def test_alembic_current_mismatch_reports_needs_migration_without_applying_it():
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
        connection.execute(text("INSERT INTO alembic_version (version_num) VALUES ('0007_prediction_snapshots')"))
    result = ops._check_alembic(_settings(), engine)
    assert result["status"] == "NEEDS_MIGRATION"
    assert result["current"] == ["0007_prediction_snapshots"]
    assert result["heads"] == [ops.EXPECTED_ALEMBIC_HEAD]
    engine.dispose()


def test_all_gates_pass_allows_only_fixed_backfill_when_explicitly_requested():
    called = []
    report = _passing_report()
    result, code = ops.execute(run_backfill=False, readiness=lambda: report,
                               backfill=lambda args: called.append(args) or 0)
    assert code == 0
    assert result["backfill"] == "ALLOWED"
    assert called == []

    result, code = ops.execute(run_backfill=True, readiness=lambda: _passing_report(),
                               backfill=lambda args: called.append(args) or 0,
                               verifier=lambda: {"objects": []})
    assert code == 0
    assert result["backfill"] == "PASS"
    assert called[-1] == list(ops.FIRST_BACKFILL_ARGS)


def test_operations_output_never_prints_secret_values(monkeypatch, capsys):
    secret_values = ("postgres-password-xyz", "r2-secret-xyz", "groq-secret-xyz")
    malicious = _passing_report()
    malicious["postgres_details"].update({"database": "macanaliz", "host": "ma-db.internal",
                                           "connection_string": secret_values[0]})
    malicious["alembic_details"] = {"status": "NEEDS_MIGRATION", "current": ["0007"], "heads": ["0008"]}
    malicious["r2_details"]["secret"] = secret_values[1]
    malicious["goaloo_details"]["api_key"] = secret_values[2]
    monkeypatch.setattr(ops, "execute", lambda **_: (malicious, 0))

    assert ops.main([]) == 0
    output = capsys.readouterr().out
    assert "DATABASE_NAME=macanaliz" in output
    assert "ALEMBIC_DETAIL=NEEDS_MIGRATION" in output
    assert all(secret not in output for secret in secret_values)


def test_backfill_failure_returns_nonzero_and_suppresses_child_output(capsys):
    def failed(_args):
        print("do-not-log-child-output")
        return 7

    result, code = ops.execute(run_backfill=True, readiness=_passing_report,
                               backfill=failed)
    assert code == 7
    assert result["backfill"] == "FAIL"
    assert "do-not-log-child-output" not in capsys.readouterr().out


def test_legacy_database_is_rejected_before_opening_connection():
    class NeverConnect:
        def connect(self):
            raise AssertionError("legacy DB must not be contacted")

    result = ops._check_postgres(
        _settings(database_url="postgresql+psycopg://user:password@host/betappv33"), NeverConnect())
    assert result["error_code"] == "LEGACY_DATABASE_REJECTED"
    assert result["connection"] == "FAIL"
