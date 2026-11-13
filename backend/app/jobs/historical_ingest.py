import argparse
from datetime import date

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.jobs.ingestion import historical_ingest
from app.jobs.ledger import SQLLedger, run_idempotent_job
from app.providers.registry import ProviderCaller, create_provider
from app.providers.base import ProviderFailure
from app.storage.r2 import R2ObjectStore


def main() -> None:
    parser = argparse.ArgumentParser(description="Resumable R2 historical match ingestion")
    parser.add_argument("--season", required=True)
    parser.add_argument("--league", required=True)
    parser.add_argument("--start-date", type=date.fromisoformat, required=True)
    parser.add_argument("--end-date", type=date.fromisoformat, required=True)
    args = parser.parse_args()
    settings = get_settings()
    try:
        provider = ProviderCaller(create_provider(settings), requests_per_minute=settings.provider_requests_per_minute,
                                  max_retries=settings.provider_max_retries)
    except ProviderFailure as exc:
        print({"status": "SKIPPED", "error_code": exc.code, "message": str(exc)})
        return
    store = R2ObjectStore()
    if SessionLocal is None:
        print({"status": "SKIPPED", "error_code": "DATABASE_NOT_CONFIGURED"})
        return
    with SessionLocal() as session:
        ledger = SQLLedger(session)
        key = f"historical_ingest:{args.season}:{args.league}:{args.start_date}:{args.end_date}"
        result = run_idempotent_job(
            ledger, key, "historical_ingest",
            lambda: historical_ingest(session, provider, store, season=args.season, league=args.league,
                                      start_date=args.start_date, end_date=args.end_date),
        )
        print(result)


if __name__ == "__main__":
    main()
