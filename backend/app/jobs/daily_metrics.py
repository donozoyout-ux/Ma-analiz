from app.jobs.tasks import run_daily

if __name__ == "__main__":
    print(run_daily("calculate_daily_metrics"))
