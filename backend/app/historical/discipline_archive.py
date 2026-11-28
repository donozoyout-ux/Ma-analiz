"""Immutable licensed-statistics R2 archiving with an independently readable receipt.

One Parquet object per league-season *source file*; the date segment is the
season partition anchor, NOT an individual match day or observation timestamp.
No download, source licensing inference, SQL ingestion or scheduled tasks here.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import date
import re

from app.historical.discipline_csv import DisciplineRow, LEAGUES, SEASONS, SOURCE
from app.storage.archive import ArchiveReceipt, archive_partition, _verify_object

DATASET = "match-statistics"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_FIELDS = ("home_corners", "away_corners", "home_yellows", "away_yellows", "home_reds", "away_reds")


def _validate_report(report: dict, permission_reference: str) -> tuple[str, str, date]:
    if not isinstance(permission_reference, str) or not 12 <= len(permission_reference.strip()) <= 800:
        raise ValueError("EXPLICIT_PERMISSION_REFERENCE_REQUIRED")
    if report.get("source") != SOURCE:
        raise ValueError("SOURCE_NOT_ALLOWLISTED")
    source_sha = report.get("file_sha256")
    if not isinstance(source_sha, str) or not _SHA256.fullmatch(source_sha):
        raise ValueError("INVALID_SOURCE_FILE_SHA256")
    league_id = report.get("league")
    season = report.get("season")
    if league_id not in {entry[0] for entry in LEAGUES.values()} or season not in SEASONS.values():
        raise ValueError("INVALID_ARCHIVE_SCOPE")
    if not isinstance(report.get("source_url"), str) or not report["source_url"].startswith(
            "https://www.football-data.co.uk/mmz4281/"):
        raise ValueError("SOURCE_URL_NOT_REVIEWED")
    expected_codes = {code: data[0] for code, data in LEAGUES.items()}
    code = next(code for code, mapped in expected_codes.items() if mapped == league_id)
    season_code = next(code for code, label in SEASONS.items() if label == season)
    expected_url = f"https://www.football-data.co.uk/mmz4281/{season_code}/{code}.csv"
    if report["source_url"] != expected_url:
        raise ValueError("SOURCE_URL_MISMATCH")
    rows = report.get("rows")
    if not isinstance(rows, list) or not 0 < len(rows) <= 500:
        raise ValueError("EMPTY_OR_OVERSIZE_SOURCE_ROWS")
    if len({row.source_key for row in rows if isinstance(row, DisciplineRow)}) != len(rows):
        raise ValueError("DUPLICATE_OR_UNKNOWN_SOURCE_IDENTITY")
    for row in rows:
        if not isinstance(row, DisciplineRow) or row.league_id != league_id or row.season != season:
            raise ValueError("SOURCE_ROW_SCOPE_MISMATCH")
    return league_id, season, date(int(season[:4]), 7, 1)


def partition_rows(report: dict, *, permission_reference: str) -> list[dict]:
    """Immutable source-reported stats, not verified match scores or odds."""
    _validate_report(report, permission_reference)
    result = []
    for row in sorted(report["rows"], key=lambda x: x.source_key):
        payload = asdict(row)
        payload["match_date"] = row.match_date.isoformat()
        payload.update(
            source=SOURCE,
            source_url=report["source_url"],
            source_file_sha256=report["file_sha256"],
            permission_reference=permission_reference.strip(),
        )
        if set(payload) != {
                "source_key", "league_id", "league_name", "season", "match_date",
                "home_team", "away_team", *_FIELDS, "source", "source_url",
                "source_file_sha256", "permission_reference"}:
            raise ValueError("UNKNOWN_STATISTICS_ARCHIVE_FIELD")
        result.append(payload)
    return result


def archive_source_file(store, tracker, *, report: dict, permission_reference: str) -> ArchiveReceipt:
    """R2 first, verify exact HEAD/GET/parquet, then ledger status VERIFIED.

    The caller must supply a SQLArchiveTracker for the SQL metadata ledger.
    Does not insert actual historical match rows; caller performs that after
    receipt validation. A partial failure leaves the source data untouched.
    """
    if tracker is None:
        raise ValueError("ARCHIVE_LEDGER_REQUIRED")
    league, season, season_anchor = _validate_report(report, permission_reference)
    rows = partition_rows(report, permission_reference=permission_reference)
    digest = report["file_sha256"]
    receipt = archive_partition(
        store, dataset=DATASET, season=season, league=league,
        day=season_anchor, rows=rows, part="source-" + digest,
        tracker=tracker, retries=1,
    )
    # Read-back is required even when the uploader reports VERIFIED.
    observed = _verify_object(
        store, receipt.key, expected_size=receipt.size,
        expected_rows=receipt.row_count, expected_checksum=receipt.checksum,
    )
    if observed != rows:
        raise IOError("R2_STATISTICS_CONTENT_VERIFICATION_FAILED")
    if receipt.row_count != len(rows):
        raise IOError("R2_STATISTICS_COUNT_MISMATCH")
    return receipt
