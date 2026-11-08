"""Long-running scheduler for bounded collection cycles."""
import json
import logging
import time
from datetime import datetime, timezone

from app.core.config import get_settings
from app.jobs.ledger import MemoryLedger, SQLLedger, prune_job_history
from app.jobs.tasks import run_task
from app.db.session import SessionLocal

settings = get_settings()
logging.basicConfig(level=settings.log_level, format="%(message)s")
logger = logging.getLogger("sports.worker")


def cycle(now: datetime | None = None) -> list[dict]:
    timestamp = now or datetime.now(timezone.utc)
    bucket = timestamp.strftime("%Y-%m-%dT%H")
    job_types = ("collect_matches", "collect_odds", "process_results")
    if SessionLocal is None:
        ledger = MemoryLedger()
        return [run_task(name, f"{name}:{bucket}", ledger) for name in job_types]
    with SessionLocal() as session:
        ledger = SQLLedger(session)
        outcomes = [run_task(name, f"{name}:{bucket}", ledger) for name in job_types]
        prune_job_history(session)
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
