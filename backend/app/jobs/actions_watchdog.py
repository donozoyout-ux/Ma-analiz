"""Read-only GitHub schedule watchdog; no DB/R2 secrets and no outbound messages."""
from datetime import datetime, timezone
import json
import os
import re
from urllib.request import Request, urlopen
from urllib.error import HTTPError

WORKFLOW = "retention-scheduler.yml"


def github_get(repo, path, token):
    url = f"https://api.github.com/repos/{repo}" + (f"/{path}" if path else "")
    request = Request(url, headers={"Authorization":f"Bearer {token}", "Accept":"application/vnd.github+json",
                                   "X-GitHub-Api-Version":"2022-11-28", "User-Agent":"MaAnalizRetentionWatchdog/1"})
    with urlopen(request, timeout=20) as response: return json.load(response)


def stamp(value):
    try: return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError, AttributeError): return None


def assess(workflow, runs, repo, *, now):
    errors, warnings, checks = [], [], {}
    if workflow.get("state") != "active": errors.append("RETENTION_WORKFLOW_NOT_ACTIVE")
    created = stamp(workflow.get("created_at"))
    for mode, minutes in (("archive", 45), ("auto", 120)):
        scheduled = [r for r in runs if r.get("event") == "schedule" and
                     r.get("display_title", "").startswith(f"Retention {mode}")]
        completed = [r for r in scheduled if r.get("conclusion") == "success"]
        success = max(completed, key=lambda r:r.get("created_at", ""), default=None)
        at = stamp(success.get("created_at")) if success else None
        age = (now-at).total_seconds() if at else None
        bootstrap = created and 0 <= (now-created).total_seconds() < minutes*60
        if age is None or age > minutes*60 or age < 0:
            (warnings if bootstrap and not success else errors).append(f"NO_RECENT_SUCCESSFUL_{mode.upper()}_SCHEDULE")
        latest = max(scheduled, key=lambda r:r.get("created_at", ""), default=None)
        if latest and latest.get("conclusion") in {"failure", "cancelled", "timed_out", "action_required", "startup_failure"}:
            errors.append(f"LATEST_{mode.upper()}_FAILED_OR_CANCELLED")
        checks[mode] = {"threshold_minutes": minutes, "last_success_age_seconds": age,
                        "latest_run_id": latest.get("id") if latest else None}
    pushed = stamp(repo.get("pushed_at"))
    if repo.get("private") is False and pushed and (now-pushed).days >= 50:
        warnings.append("PUBLIC_REPO_LAST_PUSH_OLDER_THAN_50_DAYS_REVIEW_INACTIVITY")
    return {"status": "FAILED" if errors else "PASS", "errors": errors, "warnings": warnings, "checks": checks,
            "limitation": "THIS_WATCHDOG_CAN_ALSO_BE_DISABLED_AFTER_60_DAYS;_INDEPENDENT_MONITORING_REQUIRED"}


def main():
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    token = os.environ.get("GH_TOKEN", "")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo) or not token:
        print("::error::Watchdog GitHub API configuration is missing.")
        return 1
    def get(path):
        return github_get(repo, path, token)
    try:
        workflow = get(f"actions/workflows/{WORKFLOW}")
        runs = get(f"actions/workflows/{WORKFLOW}/runs?per_page=100")["workflow_runs"]
        metadata = get("")
        result = assess(workflow, runs, metadata, now=datetime.now(timezone.utc))
    except HTTPError as exc:
        print(f"::error::Watchdog GitHub API returned HTTP {int(exc.code)}. No token or response body is logged.")
        return 1
    except Exception:
        print("::error::Watchdog could not verify workflow freshness. No token or raw API error is logged.")
        return 1
    print(json.dumps(result, sort_keys=True))
    for code in result["errors"]: print(f"::error::{code}")
    for code in result["warnings"]: print(f"::warning::{code}")
    return 1 if result["errors"] else 0


if __name__ == "__main__": raise SystemExit(main())
