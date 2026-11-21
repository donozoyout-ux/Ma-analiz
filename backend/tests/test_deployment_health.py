from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.api import routes
from app.main import app
from app.core.config import Settings


def test_liveness_does_not_call_dependency_health_checks(monkeypatch):
    monkeypatch.setattr(routes, "health", lambda: (_ for _ in ()).throw(AssertionError("diagnostics called")))
    monkeypatch.setattr(routes, "provider_health", lambda: (_ for _ in ()).throw(AssertionError("provider called")))

    response = TestClient(app).get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readiness_does_not_report_ok_when_database_is_unavailable(monkeypatch):
    settings = Settings(_env_file=None, database_url="postgresql://user:pass@example.invalid/db")
    monkeypatch.setattr(routes, "get_settings", lambda: settings)
    monkeypatch.setattr(routes, "engine", SimpleNamespace(connect=lambda: (_ for _ in ()).throw(RuntimeError("offline"))))
    monkeypatch.setattr(routes, "provider_health", lambda: {
        "provider": "UNKNOWN", "status": "PROVIDER_NOT_CONFIGURED", "coverage": {},
    })

    response = TestClient(app).get("/api/v1/health")
    payload = response.json()

    assert response.status_code == 200
    assert payload["database"] == "UNAVAILABLE"
    assert payload["status"] == "degraded"


def test_liveness_remains_responsive_when_sync_request_pool_is_full():
    import threading
    import anyio
    import httpx
    started, release = threading.Event(), threading.Event()
    def blocked_request():
        started.set()
        release.wait(3)
    async def scenario():
        limiter = anyio.to_thread.current_default_thread_limiter()
        original = limiter.total_tokens
        limiter.total_tokens = 1
        try:
            async with anyio.create_task_group() as tasks:
                tasks.start_soon(anyio.to_thread.run_sync, blocked_request)
                try:
                    with anyio.fail_after(1):
                        while not started.is_set(): await anyio.sleep(0.005)
                    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                        with anyio.fail_after(0.5):
                            response = await client.get("/health/live")
                    assert response.status_code == 200 and response.json() == {"status": "ok"}
                finally:
                    release.set()
        finally:
            limiter.total_tokens = original
    anyio.run(scenario)


def test_web_startup_does_not_import_native_arrow_before_history_is_read():
    import os
    import subprocess
    import sys
    script = '''
import os,sys
os.environ['DATABASE_URL']=''
class Guard:
 def find_spec(self, name, *args):
  if name.startswith(('pyarrow','numpy')):
   raise RuntimeError('NATIVE_ARCHIVE_LIBRARY_IMPORTED_DURING_WEB_STARTUP')
sys.meta_path.insert(0, Guard())
import app.main
'''
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
    assert result.returncode == 0, "Web startup must defer native archive imports"
