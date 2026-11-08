from app.jobs.tasks import run_daily

if __name__ == "__main__":
    print(run_daily("archive_data"))
