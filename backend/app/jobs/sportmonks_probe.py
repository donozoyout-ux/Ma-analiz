"""Read-only licensed-provider probe with an explicit historical retention verdict."""
import argparse
import json
from datetime import datetime, timedelta, timezone

from app.core.config import get_settings
from app.providers.base import ProviderFailure
from app.providers.registry import ProviderCaller
from app.providers.sportmonks import SportmonksProvider


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture-id", help="Sportmonks fixture ID, never a Goaloo ID")
    args = parser.parse_args(argv)
    cfg = get_settings()
    report = {"provider": "sportmonks", "writes": False,
              "historical_retention_days": 7, "full_2025_26_odds_archive": "NOT_SUPPORTED_BY_PUBLISHED_CONTRACT"}
    if not cfg.sportmonks_api_token:
        print(json.dumps({**report, "status": "NOT_CONFIGURED", "missing_env": ["SPORTMONKS_API_TOKEN"]}))
        return 2
    provider = SportmonksProvider(token=cfg.sportmonks_api_token, base_url=cfg.sportmonks_base_url,
                                  timeout_seconds=cfg.sportmonks_timeout_seconds, max_pages=2,
                                  requests_per_minute=min(30, cfg.provider_requests_per_minute))
    caller = ProviderCaller(provider, requests_per_minute=min(30, cfg.provider_requests_per_minute), max_retries=1)
    try:
        caller.call("health_check")
        report["authentication"] = "PASS"
        if args.fixture_id:
            match = caller.call("get_match", args.fixture_id)
            if match is None:
                report.update(status="FAILED", error_code="FIXTURE_NOT_AVAILABLE")
            elif match["start_time"] < datetime.now(timezone.utc) - timedelta(days=7):
                report.update(status="OUTSIDE_PROVIDER_RETENTION", fixture_id=args.fixture_id,
                              kickoff=match["start_time"], odds="NOT_REQUESTED")
            else:
                quotes = caller.call("get_odds", args.fixture_id)
                report.update(status="SOURCE_VERIFIED", fixture_id=args.fixture_id,
                              rows=len(quotes), prematch_rows=sum(q["captured_at"] <= match["start_time"] for q in quotes),
                              history_included=provider.historical_odds_supported,
                              odds="AVAILABLE" if quotes else "SOURCE_EMPTY")
        else:
            report.update(status="AUTHENTICATED", odds="NOT_REQUESTED", next_step="pass a verified Sportmonks --fixture-id for one recent match")
    except ProviderFailure as exc:
        report.update(status="FAILED", error_code=exc.code)
    except Exception as exc:
        report.update(status="FAILED", error_code=type(exc).__name__.upper())
    finally:
        provider.close()
    print(json.dumps(report, default=str))
    return 0 if report["status"] in {"SOURCE_VERIFIED", "AUTHENTICATED"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
