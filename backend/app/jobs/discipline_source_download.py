"""Explicitly authorized, bounded one-time three-league CSV downloader.

Does not scrape HTML or bypass access controls. Downloads only the nine
documented Football-Data season/division CSVs after the operator supplies a
permission reference. NO public data API, SQL/R2 write or auto scheduler.
Output files and SHA-256 manifest are local operator-only, not committed.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import time

import httpx

from app.historical.discipline_csv import LEAGUES, MAX_CSV_BYTES, SEASONS, preview, parse_season

BASE = "https://www.football-data.co.uk/mmz4281"
SCOPE = tuple((season, league) for season in ("2425", "2526", "2627")
              for league in ("E0", "D1", "SP1"))
MAX_REQUESTS = 9
MAX_RESPONSE_BYTES = MAX_CSV_BYTES


class SourceDownloadBlocked(RuntimeError):
    pass


def _approved_permission(reference: str) -> str:
    if not isinstance(reference, str) or len(reference.strip()) < 12:
        raise SourceDownloadBlocked("EXPLICIT_SOURCE_PERMISSION_REQUIRED")
    if reference.strip().upper().startswith(("PENDING", "TODO", "WAITING", "NONE", "UNKNOWN")):
        raise SourceDownloadBlocked("SOURCE_PERMISSION_NOT_YET_APPROVED")
    return reference.strip()


def fetch_one(client: httpx.Client, *, season: str, league: str) -> bytes:
    """At most one request, fixed HTTPS host/path; no 4xx retries or redirects."""
    if season not in SEASONS or league not in LEAGUES:
        raise SourceDownloadBlocked("UNSUPPORTED_SCOPE")
    url = f"{BASE}/{season}/{league}.csv"
    try:
        with client.stream("GET", url, follow_redirects=False,
                           headers={"Accept": "text/csv,text/plain;q=0.9,*/*;q=0.2",
                                    "User-Agent": "MA-ANALIZ/0.1 (+https://ma-analiz-2.onrender.com; authorized one-time archive)"}) as response:
            if response.status_code in (401, 403, 429):
                raise SourceDownloadBlocked("SOURCE_ACCESS_RESTRICTED_NO_BYPASS")
            if response.status_code != 200:
                raise SourceDownloadBlocked("SOURCE_NOT_AVAILABLE")
            mime = response.headers.get("content-type", "").partition(";")[0].strip().lower()
            if mime not in {"text/csv", "text/plain", "application/csv",
                            "application/octet-stream", "application/vnd.ms-excel"}:
                raise SourceDownloadBlocked("SOURCE_NOT_A_CSV")
            length = response.headers.get("content-length")
            if length:
                try:
                    if int(length) > MAX_RESPONSE_BYTES or int(length) < 0:
                        raise SourceDownloadBlocked("SOURCE_FILE_TOO_LARGE")
                except ValueError:
                    raise SourceDownloadBlocked("SOURCE_CONTENT_LENGTH_INVALID") from None
            data = bytearray()
            for part in response.iter_bytes(chunk_size=32768):
                if len(data) + len(part) > MAX_RESPONSE_BYTES:
                    raise SourceDownloadBlocked("SOURCE_FILE_TOO_LARGE")
                data.extend(part)
    except httpx.HTTPError as exc:
        raise SourceDownloadBlocked("SOURCE_NETWORK_UNAVAILABLE") from exc
    if not data:
        raise SourceDownloadBlocked("SOURCE_EMPTY_CSV")
    try:
        report_rows = parse_season(bytes(data), league=league, season_code=season)
    except ValueError as exc:
        raise SourceDownloadBlocked("SOURCE_CSV_VALIDATION_FAILED") from exc
    if not report_rows:
        raise SourceDownloadBlocked("SOURCE_HAS_NO_CORNER_OR_CARD_OBSERVATIONS")
    return bytes(data)


def download_all(*, directory: Path, permission_reference: str,
                 client: httpx.Client, pause_seconds: float = 60.0,
                 sleep=time.sleep, scopes=SCOPE) -> dict:
    """Files written once only (exclusive create); incomplete manifest not accepted.

    Never overwrite previous downloads. A failure leaves any completed files
    for manual audit, and DOES NOT produce a successful manifest. The caller
    must independently check the licence including underlying stats rights.
    """
    ref = _approved_permission(permission_reference)
    if pause_seconds < 0 or len(scopes) != MAX_REQUESTS or tuple(scopes) != SCOPE:
        raise SourceDownloadBlocked("INVALID_BOUNDED_DOWNLOAD_SCOPE")
    if directory.is_symlink():
        raise SourceDownloadBlocked("SYMLINK_DIRECTORY_NOT_SUPPORTED")
    directory = directory.resolve()
    if not directory.is_dir():
        raise SourceDownloadBlocked("DESTINATION_DIRECTORY_MISSING")
    if directory.is_symlink():
        raise SourceDownloadBlocked("SYMLINK_DIRECTORY_NOT_SUPPORTED")
    if (directory / "manifest.json").exists() or any(
        (directory / f"{season}_{league}.csv").exists() for season, league in scopes
    ):
        raise SourceDownloadBlocked("DESTINATION_ALREADY_CONTAINS_SOURCE_DATA")
    rows = []
    for i, (season, league) in enumerate(scopes):
        if i and pause_seconds > 0:
            sleep(pause_seconds)
        data = fetch_one(client, season=season, league=league)
        name = f"{season}_{league}.csv"
        with (directory / name).open("xb") as fd:
            fd.write(data)
        report = preview(directory / name, league=league, season_code=season)
        rows.append({
            "file": name, "season": report["season"], "league": report["league"],
            "sha256": sha256(data).hexdigest(), "matches_with_any_stat": report["matches_with_any_stat"],
            "complete_corners": report["complete_corners"],
            "complete_yellows": report["complete_yellows"],
            "complete_reds": report["complete_reds"],
        })
    manifest = {
        "source": "football-data.co.uk", "seasons": ["2024-2025","2025-2026","2026-2027"],
        "requested_files": MAX_REQUESTS, "received_files": len(rows),
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "permission_reference": ref, "disclaimer": "Source reported; not independent match/90-minute proof",
        "r2_uploaded": False, "sql_indexed": False, "files": rows,
    }
    with (directory / "manifest.json").open("x", encoding="utf-8") as fd:
        json.dump(manifest, fd, indent=2, ensure_ascii=False)
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description="Manual 9-file licensed CSV downloader; no R2 write")
    parser.add_argument("--output-directory", required=True, type=Path)
    parser.add_argument("--permission-reference", required=True)
    parser.add_argument("--confirm-provider-terms", action="store_true")
    parser.add_argument("--confirm-one-time-download", action="store_true")
    args = parser.parse_args(argv)
    if not args.confirm_provider_terms or not args.confirm_one_time_download:
        parser.error("Both explicit confirmations are required before requesting a source")
    try:
        with httpx.Client(timeout=20.0, follow_redirects=False, trust_env=False) as client:
            report = download_all(directory=args.output_directory,
                                  permission_reference=args.permission_reference, client=client)
        print(json.dumps({k: v for k, v in report.items() if k != "permission_reference"},
                         ensure_ascii=False, indent=2))
        return 0
    except (SourceDownloadBlocked, OSError) as exc:
        print(json.dumps({"status": "BLOCKED", "reason": str(exc) if isinstance(exc, SourceDownloadBlocked)
                          else "LOCAL_STORAGE_ERROR", "r2_uploaded": False}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
