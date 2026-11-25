"""Manually gated independent Bundesliga SQL result verification.

Default dry-run fetches BOTH official APIs, then checks existing SQL fixture/result.
No scheduled execution, no SQL deletion. Live mode requires an explicit approval
phrase, independent R2 proof and database credentials.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import json
import os
import sys

import httpx

from app.jobs.independent_result_persistence import VerificationBlocked, verify_bundesliga_result
from app.providers.football_data_org import fetch_bundesliga_finals
from app.providers.result_sources import parse_openligadb


def collect(day: date, matchday: int, *, secondary_fetch=fetch_bundesliga_finals,
            client_factory=httpx.Client, now=None):
    now = now or datetime.now(timezone.utc)
    if day.year not in (2026, 2027) or not 1 <= matchday <= 34:
        raise ValueError("INVALID_BUNDESLIGA_SCOPE")
    season = day.year if day.month >= 7 else day.year - 1
    if season != 2026:
        raise ValueError("ONLY_2026_2027_SEASON_ALLOWED")
    url = f"https://api.openligadb.de/getmatchdata/bl1/{season}/{matchday}"
    with client_factory(timeout=8, follow_redirects=False) as client:
        try:
            response = client.get(url)
            response.raise_for_status()
            if len(response.content) > 512_000:
                raise ValueError("OPENLIGADB_RESPONSE_TOO_LARGE")
            payload = response.json()
        except (httpx.HTTPError, ValueError):
            raise ValueError("OPENLIGADB_FETCH_FAILED") from None
    first = parse_openligadb(payload, day, observed_at=now, source_url=url)
    second = secondary_fetch(day, observed_at=now)
    return first, second


def run(day: date, matchday: int, *, dry_run=True, live_approved=False,
        session=None, store=None, primary=None, secondary=None, now=None):
    now = now or datetime.now(timezone.utc)
    if not dry_run and not live_approved:
        raise VerificationBlocked("LIVE_OPERATOR_APPROVAL_REQUIRED")
    if not dry_run and store is None:
        raise VerificationBlocked("LIVE_R2_REQUIRED")
    if primary is None or secondary is None:
        primary, secondary = collect(day, matchday, now=now)
    if len(primary) != 1 or len(secondary) != 1:
        raise VerificationBlocked("ONE_FINISHED_MATCH_PER_SOURCE_REQUIRED")
    if session is None:
        raise VerificationBlocked("POSTGRES_SESSION_REQUIRED")
    result = verify_bundesliga_result(session, store, primary, secondary,
        now=now, dry_run=dry_run, live_approved=live_approved)
    return {"operation": "BUNDESLIGA_INDEPENDENT_SCORE_VERIFICATION",
            "date": day.isoformat(), "matchday": matchday,
            "source_count": 2, "primary_count": len(primary),
            "secondary_count": len(secondary), "result": result,
            "auto_retention_delete": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--day", type=date.fromisoformat, required=True)
    parser.add_argument("--matchday", type=int, choices=range(1, 35), required=True)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--approval", default="")
    args = parser.parse_args(argv)
    if args.live and (args.approval != "VERIFY_ONE_MATCH_ONLY" or
            os.getenv("AUTO_RETENTION_DELETE_ENABLED", "false").lower() == "true" or
            os.getenv("OPERATIONAL_DELETE_ENABLED", "false").lower() == "true"):
        print(json.dumps({"status": "BLOCKED", "reason": "LIVE_GATES_NOT_APPROVED"}))
        return 2
    from app.db.session import SessionLocal
    from app.core.config import get_settings
    from app.storage.r2 import R2ObjectStore
    settings = get_settings()
    if SessionLocal is None or not settings.database_url_valid:
        print(json.dumps({"status": "BLOCKED", "reason": "DATABASE_NOT_CONFIGURED"}))
        return 2
    if args.live and not settings.r2_configured:
        print(json.dumps({"status": "BLOCKED", "reason": "R2_NOT_CONFIGURED"}))
        return 2
    try:
        with SessionLocal() as session:
            report = run(args.day, args.matchday, dry_run=not args.live,
                live_approved=args.live, session=session,
                store=R2ObjectStore(settings=settings) if args.live else None)
            if not args.live:
                session.rollback()
    except (VerificationBlocked, ValueError, RuntimeError, IOError) as exc:
        # Only a fixed, sanitized reason code. Never expose connection strings.
        reason = str(exc)
        if not reason.isupper() or len(reason) > 100 or not reason.replace("_", "").isalnum():
            reason = "VERIFICATION_FAILED"
        print(json.dumps({"status": "BLOCKED", "reason": reason, "sql_delete": False}))
        return 2
    except Exception:
        print(json.dumps({"status": "ERROR", "reason": "VERIFICATION_EXECUTION_FAILED", "sql_delete": False}))
        return 1
    print(json.dumps(report, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
