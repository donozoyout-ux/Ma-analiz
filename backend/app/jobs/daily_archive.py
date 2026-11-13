from datetime import datetime, timezone
import json

from app.jobs.tasks import run_daily

if __name__ == "__main__":
    today = datetime.now(timezone.utc).date()
    yesterday = today - __import__("datetime").timedelta(days=1)
    results = [run_daily("process_results", yesterday), run_daily("archive_data", today),
               run_daily("cleanup_database", today), run_daily("calculate_daily_metrics", yesterday)]
    print(json.dumps({"jobs": results}, default=str))
