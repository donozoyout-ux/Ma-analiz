"""Keep pytest and its child processes isolated from the developer's secrets."""
import os

import pytest

# Applied before test module collection and inherited by test-created subprocesses.
os.environ["MAC_ANALIZ_TEST_MODE"] = "1"
for key in (
    "DATABASE_URL", "SEASON_BULK_OPERATOR_DATABASE_URL", "SEASON_BULK_PREFLIGHT_DATABASE_URL",
    "R2_ENDPOINT", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET",
    "PROVIDER_API_KEY", "SPORTMONKS_API_TOKEN", "AI_API_KEY", "GROQ_API_KEY",
    "TELEGRAM_BOT_TOKEN", "SYSTEM_DIAGNOSTICS_TOKEN", "CONTROLLED_IMPORT_OPERATOR_TOKEN_SHA256",
):
    os.environ.pop(key, None)



# Only repository CI's disposable local databases are permitted.
ALLOWED_TEST_URLS = {
    "BUNDESLIGA_TEST_DATABASE_URL": {"postgresql+psycopg://pilot:pilot-test-only@localhost:5432/pilot"},
    "SEASON_BULK_TEST_DATABASE_URL": {"postgresql+psycopg://test:test-only@localhost:5432/season_test"},
    "TEST_POSTGRES_URL": {
        "postgresql+psycopg://goaloo_test:goaloo_test_only@127.0.0.1:5432/goaloo_ci",
        "postgresql+psycopg://stage7h_test:stage7h_local_only@127.0.0.1:5432/stage7h_ci",
    },
}
for key, allowed in ALLOWED_TEST_URLS.items():
    if key in os.environ and os.environ[key] not in allowed:
        raise RuntimeError(f"Unsafe test database configuration: {key}")


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    if report.failed:
        report.longrepr = "Failure details suppressed to prevent credential disclosure; reproduce with synthetic inputs."
    report.sections.clear()


@pytest.hookimpl(hookwrapper=True)
def pytest_make_collect_report(collector):
    outcome = yield
    report = outcome.get_result()
    if report.failed:
        report.longrepr = "Collection failure details suppressed to prevent credential disclosure."
    report.sections.clear()
