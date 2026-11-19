from datetime import datetime, timezone
import json
import sys

from app.jobs.tasks import run_daily


def _failed_for_production(item: dict) -> bool:
    status = item.get("status")
    if status in {"FAILED", "PARTIAL"}:
        return True
    if status != "SKIPPED":
        return False
    failure_codes = {item.get("error_code"), item.get("reason")}
    return any(code in {
        "DATABASE_NOT_CONFIGURED", "R2_NOT_CONFIGURED", "PROVIDER_NOT_CONFIGURED",
        "NO_ENABLED_LEAGUES_AVAILABLE", "HISTORICAL_SCOPE_NOT_CONFIGURED",
    } for code in failure_codes if code)


def _run(job_type: str, day) -> dict:
    try:
        return run_daily(job_type, day)
    except Exception as exc:
        return {"job": job_type, "status": "FAILED", "error_code": type(exc).__name__.upper()}


def main() -> int:
    today = datetime.now(timezone.utc).date()
    yesterday = today - __import__("datetime").timedelta(days=1)
    results = [_run("process_results", yesterday), _run("archive_data", today),
               _run("cleanup_database", today), _run("calculate_daily_metrics", yesterday)]
    print(json.dumps({"jobs": results}, default=str), flush=True)
    return 1 if any(_failed_for_production(item) for item in results) else 0


if __name__ == "__main__":
    sys.exit(main())
