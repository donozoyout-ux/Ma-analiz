from datetime import datetime, timezone

from app.jobs.tasks import run_daily

if __name__ == "__main__":
    print(run_daily("process_results", datetime.now(timezone.utc).date()))
