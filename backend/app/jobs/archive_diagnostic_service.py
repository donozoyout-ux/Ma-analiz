"""Temporary web start command for bounded, secret-safe archive diagnostics.

Uses the existing web service and PORT. No normal app/worker/cron is started.
The process stays healthy after one diagnostic attempt to avoid restart loops.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import logging
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


SMOKE_SCOPE = ("36", "2025-2026", "2025-08-15")
STATUSES = {"NOT_RUN", "NOT_VERIFIED", "PASS", "FAIL", "FAILED", "BLOCKED", "CONNECTED",
            "NOT_CONFIGURED", "NEEDS_MIGRATION", "CONFIGURED_NOT_VERIFIED", "SMOKE_PASS", "CONNECTIONS_PASS"}
ENV_NAMES = {"DATABASE_URL", "R2_ENDPOINT", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET"}


def _status(value):
    return value if isinstance(value, str) and value in STATUSES else "NOT_VERIFIED"


def _summary(report, mode):
    """Only fixed statuses and counters can reach logs/public responses."""
    pg = report.get("postgres", {})
    r2 = report.get("r2", {})
    result = {"event": "archive_diagnostic", "mode": mode, "status": _status(report.get("status")),
              "postgres": {key: _status(pg.get(key)) for key in ("connection", "schema")},
              "r2": {key: _status(r2.get(key)) for key in ("status", "write", "read", "size", "checksum", "parquet_rows", "delete")},
              "missing_env": sorted(set(report.get("missing_env", [])) & ENV_NAMES),
              "invalid_env": sorted(set(report.get("invalid_env", [])) & ENV_NAMES),
              "bulk": "NOT_RUN", "migration": "NOT_RUN", "postgres_cleanup": "NOT_RUN"}
    if mode == "smoke":
        result["scope"] = {"league": SMOKE_SCOPE[0], "season": SMOKE_SCOPE[1], "date": SMOKE_SCOPE[2], "odds_requested": False}
    counts = report.get("verified_r2_rows", {})
    result["verified_r2_rows"] = {key: counts[key] for key in ("matches", "results", "statistics")
                                  if type(counts.get(key)) is int and counts[key] >= 0}
    result["ready_for_smoke"] = (result["postgres"] == {"connection": "PASS", "schema": "PASS"}
                                  and result["r2"]["status"] == "CONNECTED")
    return result


def run_diagnostic(mode):
    """Capture dependency output, including validation errors, never raw values."""
    if mode not in {"connections", "smoke"}:
        raise ValueError("Invalid diagnostic mode")
    capture = io.StringIO()
    disabled = logging.root.manager.disable
    try:
        logging.disable(logging.CRITICAL)
        with contextlib.redirect_stdout(capture), contextlib.redirect_stderr(capture):
            from app.core.config import get_settings
            from app.jobs.archive_preflight import check_postgres, main as preflight
            cfg = get_settings()
            missing = list(cfg.r2_missing_env) + (["DATABASE_URL"] if cfg.database_url_missing else [])
            invalid = list(cfg.r2_invalid_env) + (["DATABASE_URL"] if not cfg.database_url_missing and not cfg.database_url_valid else [])
            report = {"status": "NOT_CONFIGURED", "missing_env": missing, "invalid_env": invalid}
            if not missing and not invalid:
                if mode == "connections":
                    report["postgres"] = check_postgres(cfg)
                    report["status"] = "FAILED"
                    if report["postgres"]["connection"] == "PASS":
                        from app.jobs.r2_smoke_test import run_smoke
                        report["r2"] = run_smoke(parquet=True)
                        if report["r2"]["status"] == "CONNECTED":
                            report["status"] = "CONNECTIONS_PASS"
                else:
                    league, season, day = SMOKE_SCOPE
                    capture.seek(0)
                    capture.truncate(0)
                    preflight(["--smoke", "--league", league, "--season", season, "--date", day])
                    report = json.loads(capture.getvalue().strip().splitlines()[-1])
        return _summary(report, mode)
    except Exception:
        # No exception messages, traceback, config values or dependency output.
        return _summary({"status": "FAILED"}, mode)
    finally:
        logging.disable(disabled)


class DiagnosticState:
    def __init__(self, mode, runner=run_diagnostic):
        self.mode, self.runner = mode, runner
        self.lock = threading.Lock()
        self.started = False
        self.result = None

    def start_once(self):
        with self.lock:
            if self.started:
                return False
            self.started = True
        threading.Thread(target=self._run, daemon=True, name="archive-diagnostic").start()
        return True

    def _run(self):
        try:
            result = self.runner(self.mode)
        except Exception:
            result = _summary({"status": "FAILED"}, self.mode)
        with self.lock:
            self.result = result
        print(json.dumps(result, sort_keys=True), flush=True)

    def public_status(self):
        with self.lock:
            result = self.result
            return {"service": "archive-diagnostic", "mode": self.mode,
                    "state": "finished" if result else "running" if self.started else "waiting",
                    "result": result["status"] if result else "NOT_RUN"}


def make_server(port, state, host="0.0.0.0"):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/health/live":
                payload, code = {"status": "ok"}, 200
            elif self.path in {"/", "/archive-diagnostic"}:
                payload, code = state.public_status(), 200
            else:
                payload, code = {"status": "not_found"}, 404
            body = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass
    return ThreadingHTTPServer((host, port), Handler)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", required=True, choices=("connections", "smoke"))
    args = parser.parse_args(argv)
    try:
        port = int(os.environ.get("PORT", "10000"))
        if not 1 <= port <= 65535:
            raise ValueError
        state = DiagnosticState(args.mode)
        server = make_server(port, state)
    except Exception:
        print('{"event":"archive_diagnostic","status":"FAILED","server":"NOT_STARTED"}', flush=True)
        return 1
    state.start_once()
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
