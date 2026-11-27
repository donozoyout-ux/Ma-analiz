"""Public season-file dry run or isolated SQLite and in-memory object storage rehearsal."""
import argparse
from contextlib import ExitStack
import json

import httpx

from app.providers.season_files import DownloadBudget, LEAGUES, SEASONS, download, parse_season, source_for, summarize_source


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--league", choices=LEAGUES, action="append")
    parser.add_argument("--season", choices=SEASONS, action="append")
    parser.add_argument("--isolated", action="store_true", help="Rehearse with transient SQLite and simulated R2")
    args = parser.parse_args()
    budget = DownloadBudget()
    failure = False
    with ExitStack() as stack:
        client = stack.enter_context(httpx.Client(trust_env=False))
        if args.isolated:
            from sqlalchemy import create_engine
            from sqlalchemy.orm import Session

            from app.historical.season_transfer import transfer_source
            from app.models.season_bulk import SEASON_TABLES
            from app.storage.r2 import InMemoryObjectStore

            engine = create_engine("sqlite://")
            stack.callback(engine.dispose)
            for table in SEASON_TABLES:
                table.create(engine)
            session = stack.enter_context(Session(engine))
            store = InMemoryObjectStore()
        for league in dict.fromkeys(args.league or ("uefa.cl", "uefa.el", "uefa.ecl")):
            for season in dict.fromkeys(args.season or SEASONS):
                source = source_for(league, season)
                if source is None:
                    status = ("BLOCKED_LICENSE_REVIEW" if (league, season) == ("uefa.ecl", "2025-2026")
                              else "SOURCE_UNAVAILABLE")
                    print(json.dumps({"league": league, "season": season, "status": status}))
                    continue
                try:
                    if args.isolated:
                        with session.begin():
                            report = transfer_source(session, store, source, client, budget,
                                                     approval="IMPORT_REVIEWED_SEASON_ONLY")
                        report["mode"] = "ISOLATED"
                    else:
                        raw = download(source, client, budget)
                        report = summarize_source(source, raw, parse_season(source, raw))
                except (httpx.HTTPError, ValueError, KeyError, TypeError, RuntimeError) as exc:
                    report = {"league": league, "season": season, "status": "SOURCE_ERROR", "error": str(exc)}
                    failure = True
                print(json.dumps(report, ensure_ascii=True))
    return int(failure)


if __name__ == "__main__":
    raise SystemExit(main())
