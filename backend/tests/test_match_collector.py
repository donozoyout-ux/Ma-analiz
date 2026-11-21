"""Synthetic transport/collector tests. None of these fixtures are live evidence."""
import base64
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import httpx
import pytest

from app.jobs.match_collector import collect, local_state, main
from app.providers.collector_http import CollectorHTTP, EvidenceFailure, valid_url
from app.providers.collector_policy import policy_digest


class Clock:
    def __init__(self):
        self.at = datetime(2026, 11, 21, 20, tzinfo=timezone.utc)
        self.sleeps = []

    def __call__(self):
        return self.at

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.at += timedelta(seconds=seconds)


URL = "https://api.openligadb.de/getcurrentgroup/bl1"


def transport(handler, state=None):
    clock = Clock()
    http = CollectorHTTP({} if state is None else state, client=httpx.Client(transport=httpx.MockTransport(handler)),
                         clock=clock, sleep=clock.sleep, jitter=lambda: 0.5)
    return http, clock


def simple(status=200, **kwargs):
    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(status, **kwargs)
    return handler


@pytest.mark.parametrize("url", ["http://api.openligadb.de/a", "https://user:password@api.openligadb.de/a",
    "https://api.openligadb.de/a?token=secret", "https://api.openligadb.de:443/a", "https://evil.example/a",
    "https://api.openligadb.de/a#secret"])
def test_sensitive_or_unapproved_url_is_never_requested(url):
    with pytest.raises(EvidenceFailure, match="UNAPPROVED_PUBLIC_URL"):
        valid_url(url)


def test_api_rate_limit_jitter_and_persistent_rate_clock():
    state = {}
    h, clock = transport(simple(json={"ok": True}), state)
    h.jitter = iter([0.0, 1.9, 0.0]).__next__
    h.get("one", URL)
    h.get("two", URL)
    stamps = [datetime.fromisoformat(r["observed_at"]) for r in h.requests if not r["resource"].startswith("robots:")]
    assert (stamps[1] - stamps[0]).total_seconds() >= 60
    assert 1.9 in clock.sleeps
    assert state["last_api_request"]


@pytest.mark.parametrize("status", [401, 403, 429, 302])
def test_denial_has_no_immediate_retry_and_blocks_next_run(status):
    state = {}
    h, _ = transport(simple(status, headers={"Retry-After": "86400"}), state)
    with pytest.raises(EvidenceFailure):
        h.get("one", URL)
    assert len(h.requests) == 2
    h2, _ = transport(simple(), state)
    with pytest.raises(EvidenceFailure, match="ORIGIN_BLOCKED"):
        h2.get("other", URL)
    assert not h2.requests


def test_retry_after_http_date_is_honoured():
    h, clock = transport(simple(429, headers={"Retry-After": "Mon, 23 Nov 2026 20:00:00 GMT"}))
    with pytest.raises(EvidenceFailure):
        h.get("one", URL)
    until = datetime.fromisoformat(h.blocks["api.openligadb.de"]["until"])
    assert until >= datetime(2026, 11, 23, 20, tzinfo=timezone.utc)


def test_robots_disallow_stops_before_data():
    h, _ = transport(lambda r: httpx.Response(200, text="User-agent: *\nDisallow: /getcurrentgroup\n"))
    with pytest.raises(EvidenceFailure, match="ROBOTS_DISALLOWED"):
        h.get("one", URL)
    assert len(h.requests) == 1


@pytest.mark.parametrize("error", [httpx.ConnectError("password=DO_NOT_LOG"), httpx.ReadTimeout("secret")])
def test_network_errors_are_safe_and_retries_bounded(error):
    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        raise error
    h, _ = transport(handler)
    with pytest.raises(EvidenceFailure) as caught:
        h.get("one", URL)
    assert "secret" not in str(caught.value) and "password" not in str(caught.value)
    assert len(h.requests) == (3 if isinstance(error, httpx.TimeoutException) else 2)


def test_server_retry_backoff_and_then_success():
    attempts = []
    def handler(r):
        if r.url.path == "/robots.txt":
            return httpx.Response(404)
        attempts.append(r)
        return httpx.Response(503 if len(attempts) == 1 else 200, json={"ok": True})
    h, clock = transport(handler)
    body, _ = h.get("one", URL)
    assert json.loads(body) == {"ok": True} and len(attempts) == 2
    assert 2.5 in clock.sleeps


def test_long_retry_after_defers_instead_of_spinning():
    h, _ = transport(simple(503, headers={"Retry-After": "3600"}))
    with pytest.raises(EvidenceFailure, match="SERVER_BACKOFF"):
        h.get("one", URL)
    assert len(h.requests) == 2


def test_conditional_cache_checksum_and_original_observation_time():
    def handler(r):
        if r.url.path == "/robots.txt":
            return httpx.Response(404)
        if r.headers.get("If-None-Match") == '"test"':
            return httpx.Response(304)
        return httpx.Response(200, json={"ok": True}, headers={"ETag": '"test"'})
    h, clock = transport(handler)
    first, meta = h.get("one", URL)
    clock.sleep(3600)
    second, cached = h.get("one", URL)
    assert first == second and cached["cached"] and cached["received_at"] == meta["received_at"]
    h.cache["one"]["body"] = base64.b64encode(b"tampered").decode()
    with pytest.raises(EvidenceFailure, match="CACHE_CHECKSUM_INVALID"):
        h.get("one", URL)


def environment():
    policies = {"expires_on": "2026-12-21", "documents": {}}
    docs = {"openligadb_licence": "https://www.openligadb.de/lizenz",
            "openligadb_terms": "https://www.openligadb.de/nutzungsbedingungen",
            "openfootball_licence": "https://raw.githubusercontent.com/openfootball/football.json/master/LICENSE.md"}
    for key, url in docs.items():
        policies["documents"][key] = {"url": url, "sha256": hashlib.sha256(key.encode()).hexdigest()}
    def handler(request):
        assert request.method == "GET" and "authorization" not in request.headers
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        for key, url in docs.items():
            if str(request.url) == url:
                return httpx.Response(200, text=key)
        if "/getcurrentgroup/" in request.url.path:
            return httpx.Response(200, json={"groupOrderID": 10 if request.url.path.endswith("bl1") else 11})
        if request.url.host == "raw.githubusercontent.com":
            german = request.url.path.endswith("de.1.json")
            return httpx.Response(200, json={"name": "Deutsche Bundesliga 2026/27" if german else "English Premier League 2026/27",
                "matches": [{"date": "2026-11-21", "time": "15:00", "team1": "Synthetic Home", "team2": "Synthetic Away"}]})
        shortcut = request.url.path.split("/")[2]
        league, id_, round_ = {"bl1": (4937, 100, 10), "pl": (5996, 200, 11), "pl1": (5993, 300, 11)}[shortcut]
        return httpx.Response(200, json=[{"matchID": id_, "leagueId": league, "leagueShortcut": shortcut,
            "leagueSeason": 2026, "group": {"groupOrderID": round_}, "matchDateTimeUTC": "2026-11-21T12:00:00Z",
            "team1": {"teamId": 40, "teamName": "Synthetic Home"}, "team2": {"teamId": 65, "teamName": "Synthetic Away"},
            "matchIsFinished": False, "matchResults": []}])
    return policies, handler


def run_synthetic(state=None, wrapper=None):
    policies, handler = environment()
    state = {} if state is None else state
    h, clock = transport(wrapper(handler) if wrapper else handler, state)
    if state.get("last_completed_at"):
        clock.at = max(clock.at, datetime.fromisoformat(state["last_completed_at"].replace("Z", "+00:00")) + timedelta(minutes=10))
    return collect(state, http=h, clock=clock, policies=policies), state


def test_collector_end_to_end_quarantine_and_duplicate_replay(monkeypatch):
    monkeypatch.setenv("AUTO_RETENTION_DELETE_ENABLED", "true")
    monkeypatch.setenv("DATABASE_URL", "postgresql://secret:password@never-contact")
    report, state = run_synthetic()
    replay, _ = run_synthetic(state)
    assert report["SQL_WRITE"] == report["R2_WRITE"] == report["LIVE_DELETE"] == "NOT_RUN"
    assert not report["production_ready"] and not report["AUTO_RETENTION_DELETE_ENABLED"]
    assert report["evidence_kind"] == "INJECTED_TEST_TRANSPORT"
    assert report["counts"]["NEW"] == replay["counts"]["UNCHANGED"] == 5
    assert replay["counts"]["NEW"] == replay["counts"]["UPDATED"] == 0
    assert report["leagues"]["tr.sl"]["mode"] == "NOT_AVAILABLE"
    assert not report["leagues"]["en.pl"]["production_provider_selected"]
    for result in report["leagues"].values():
        for row in result["observations"]:
            assert row["validation_status"] == "REVIEW_REQUIRED"
            assert row["verified_final_score"] is None
            assert len(row["raw_response_sha256"]) == 64
    assert "secret:password" not in json.dumps(report)


def test_licence_change_prevents_provider_data_fetch():
    def wrap(handler):
        def custom(r):
            if r.url.path == "/lizenz":
                return httpx.Response(200, text="Changed permission terms")
            assert r.url.host != "api.openligadb.de"
            return handler(r)
        return custom
    report, _ = run_synthetic(wrapper=wrap)
    assert any(e["error_code"] == "LICENCE_OR_TERMS_CHANGED" for e in report["source_errors"])


@pytest.mark.parametrize("change", ["duplicate", "utc", "teams", "league"])
def test_bad_source_rows_are_rejected(change):
    def wrap(handler):
        def custom(r):
            response = handler(r)
            if "/getmatchdata/bl1/" in r.url.path:
                payload = response.json()
                if change == "duplicate":
                    payload += [{**payload[0], "matchIsFinished": True}]
                elif change == "utc":
                    payload[0]["matchDateTimeUTC"] = "2026-11-21T12:00:00"
                elif change == "teams":
                    payload[0]["team2"]["teamId"] = 40
                else:
                    payload[0]["leagueId"] = 5996
                return httpx.Response(200, json=payload)
            return response
        return custom
    report, _ = run_synthetic(wrapper=wrap)
    assert any(e["error_code"] == "SOURCE_SCHEMA_IDENTITY_OR_UTC_INVALID" for e in report["source_errors"])


def test_changed_source_and_utc_conflict_are_reported():
    first, state = run_synthetic()
    def wrap(handler):
        def custom(r):
            response = handler(r)
            if "/getmatchdata/pl1/" in r.url.path:
                rows = response.json(); rows[0]["matchDateTimeUTC"] = "2026-11-21T14:00:00Z"
                return httpx.Response(200, json=rows)
            return response
        return custom
    changed, _ = run_synthetic(state, wrap)
    assert changed["counts"]["UPDATED"] == 1
    assert any(c["status"] == "CONFLICT" for c in changed["leagues"]["en.pl"]["comparisons"])
    assert not any(c["mapping_accepted"] for c in changed["leagues"]["en.pl"]["comparisons"])


def test_lock_corrupt_state_and_interrupted_write_safety(tmp_path):
    with local_state(tmp_path) as state:
        state["marker"] = "public-test"
        with pytest.raises(EvidenceFailure, match="LOCK_BUSY"):
            with local_state(tmp_path):
                pass
    with pytest.raises(RuntimeError):
        with local_state(tmp_path) as state:
            state["marker"] = "must-not-commit"
            raise RuntimeError("interrupted")
    assert json.loads((tmp_path / "state.json").read_text())["marker"] == "public-test"
    (tmp_path / "state.json").write_text("bad")
    with pytest.raises(EvidenceFailure, match="LOCAL_STATE_INVALID"):
        with local_state(tmp_path):
            pass


def test_out_of_scope_date_expired_licence_and_gap_warning():
    h, clock = transport(simple())
    with pytest.raises(EvidenceFailure, match="ONLY_TODAY_OR_YESTERDAY"):
        collect({}, day=clock().date() - timedelta(days=2), http=h, clock=clock)
    report = collect({}, http=h, clock=clock, policies={"expires_on": "2026-11-20"})
    assert report["status"] == "BLOCKED" and not h.requests
    state = {"last_completed_at": "2026-11-20T00:00:00Z"}
    report, _ = run_synthetic(state)
    assert "COLLECTOR_GAP_OVER_18_HOURS" in report["warnings"]


def test_no_import_of_sql_r2_settings_or_paid_clients():
    code = '''
import sys
class Guard:
 def find_spec(self, fullname, *args):
  if fullname.startswith(("app.db", "app.storage", "app.core.config", "sqlalchemy", "boto3", "pyarrow")):
   raise RuntimeError("FORBIDDEN_IMPORT")
sys.meta_path.insert(0, Guard())
import app.jobs.match_collector
'''
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, "Collector must not load production infrastructure"


def test_cli_has_no_import_or_persistence_switch():
    with pytest.raises(SystemExit) as caught:
        main(["--persist"])
    assert caught.value.code == 2


def test_changed_team_identity_remains_conflict_on_replay():
    _, state = run_synthetic()
    def wrap(handler):
        def custom(r):
            response = handler(r)
            if "/getmatchdata/bl1/" in r.url.path:
                rows = response.json(); rows[0]["team1"]["teamId"] = 999
                return httpx.Response(200, json=rows)
            return response
        return custom
    for _ in range(2):
        report, _ = run_synthetic(state, wrap)
        rows = report["leagues"]["de.bl1"]["observations"]
        match = next(r for r in rows if r["provider"] == "openligadb")
        assert match["validation_status"] == "CONFLICT"
        assert "SOURCE_IDENTITY_CHANGED" in match["quarantine_reasons"]


@pytest.mark.parametrize("case", ["budget", "oversize", "future_clock", "invalid_robots"])
def test_resource_and_cache_limits_fail_closed(case):
    h, clock = transport(simple(content=b"a" * (1_000_001 if case == "oversize" else 1)))
    if case == "budget":
        h.requests = [{}] * 20
    elif case == "future_clock":
        h.state["last_api_request"] = (clock() + timedelta(days=100)).isoformat()
    elif case == "invalid_robots":
        h.client = httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b"\xff")))
    with pytest.raises(EvidenceFailure):
        h.get("one", URL)


def legal_document(text="Commercial use allowed", link="https://example.org/licence", token="first"):
    return (f'<nav>Volatile navigation</nav><div class="card-body main-card-body"><!--{token}-->'
            f'<h3>Licence</h3><p>{text} ' + 'Legal conditions apply. ' * 30 +
            f'</p><a href="{link}">Full licence</a><script>nonce={token}</script></div>').encode()


def test_legal_hash_ignores_transport_tokens_but_not_legal_text_or_links():
    mode = "html-main-card-visible-links-v1"
    before = policy_digest(legal_document(), mode)
    assert policy_digest(legal_document(token="different-blazor-token"), mode) == before
    assert policy_digest(legal_document(text="Non-commercial only"), mode) != before
    assert policy_digest(legal_document(link="https://example.org/restricted"), mode) != before


@pytest.mark.parametrize("body", [b"<html>New page layout</html>", legal_document() * 2])
def test_legal_layout_change_blocks_automatic_permission_assumption(body):
    with pytest.raises(ValueError, match="STRUCTURE_CHANGED"):
        policy_digest(body, "html-main-card-visible-links-v1")


def test_local_audit_failure_never_prints_raw_exception(monkeypatch, tmp_path, capsys):
    import app.jobs.match_collector as module
    def broken(*args, **kwargs):
        raise PermissionError("postgresql://user:password@secret.invalid")
    monkeypatch.setattr(module, "local_state", broken)
    monkeypatch.setattr(module, "atomic_json", broken)
    assert main(["--state-dir", str(tmp_path)]) == 1
    output = capsys.readouterr().out
    assert "LOCAL_AUDIT_WRITE_FAILED" in output
    assert "password" not in output and "Traceback" not in output
