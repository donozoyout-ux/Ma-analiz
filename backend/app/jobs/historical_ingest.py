"""Compatibility entry point for the validated, date-checkpointed backfill CLI.

Both commands share scope validation, safe errors and exit status. Range-level
job receipts cannot hide incomplete per-day field checkpoints.
"""
from app.jobs.historical_backfill import main


if __name__ == "__main__":
    raise SystemExit(main())
