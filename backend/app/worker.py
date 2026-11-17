"""Long-running scheduler for bounded collection cycles."""
import json
import logging
import time
from datetime import datetime, timezone

from app.core.config import get_settings
from app.jobs.ledger import MemoryLedger, SQLLedger, prune_job_history, run_idempotent_job
from app.jobs.tasks import run_task
from app.db.session import SessionLocal

settings = get_settings()
logging.basicConfig(level=settings.log_level, format="%(message)s")
logger = logging.getLogger("sports.worker")


def cycle(now: datetime | None = None) -> list[dict]:
    timestamp = now or datetime.now(timezone.utc)
    job_specs = (
        ("collect_matches", settings.worker_match_interval_seconds),
        ("collect_odds", settings.worker_odds_interval_seconds),
        ("process_results", settings.worker_results_interval_seconds),
        ("scheduled_scan", settings.worker_scanner_interval_seconds),
    )
    scheduled = []
    for job_type, raw_interval in job_specs:
        interval = max(60, raw_interval)
        slot_seconds = int(timestamp.timestamp() // interval) * interval
        slot = datetime.fromtimestamp(slot_seconds, tz=timezone.utc)
        scheduled.append((job_type, f"{job_type}:{slot.isoformat()}"))
    if SessionLocal is None:
        ledger = MemoryLedger()
        return [run_task(name, key, ledger) for name, key in scheduled]
    with SessionLocal() as session:
        ledger = SQLLedger(session)
        outcomes = [run_task(name, key, ledger) for name, key in scheduled]
        prune_interval = max(60, settings.worker_prune_interval_seconds)
        prune_slot = int(timestamp.timestamp() // prune_interval) * prune_interval
        prune_key = f"prune_job_history:{datetime.fromtimestamp(prune_slot, tz=timezone.utc).date().isoformat()}"
        outcomes.append(run_idempotent_job(
            ledger, prune_key, "prune_job_history",
            lambda: {"status": "SUCCESS", "records_written": prune_job_history(session)},
        ))
        return outcomes


def main() -> None:
    while True:
        try:
            results = cycle()
            logger.info(json.dumps({"event": "worker_cycle", "timestamp": datetime.now(timezone.utc).isoformat(), "jobs": results}))
        except Exception as exc:
            logger.error(json.dumps({"event": "worker_cycle_failed", "error": type(exc).__name__}))
        time.sleep(max(30, settings.worker_interval_seconds))


if __name__ == "__main__":
    main()
