import json
from datetime import datetime, timezone

from app.jobs.tasks import run_daily


if __name__ == "__main__":
    today = datetime.now(timezone.utc).date()
    results = [
        run_daily("collect_matches", today),
        run_daily("collect_odds", today),
        run_daily("process_results", today),
        run_daily("archive_data", today),
        run_daily("cleanup_database", today),
        run_daily("calculate_daily_metrics", today),
    ]
    print(json.dumps({"date": today.isoformat(), "jobs": results}, default=str))
