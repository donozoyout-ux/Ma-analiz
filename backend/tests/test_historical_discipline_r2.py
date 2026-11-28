"""No remote R2 or source CSV: synthetic in-memory archive integrity tests."""
from dataclasses import replace
from datetime import date
import hashlib

import pytest

from app.historical.discipline_archive import archive_source_file, partition_rows
from app.historical.discipline_csv import DisciplineRow
from app.storage.archive import ArchiveStatus, validate_transition
from app.storage.r2 import InMemoryObjectStore

PERMISSION = "SYNTHETIC-ISOLATED-TEST-PERMISSION"
ROW = DisciplineRow(
    "a"*64, "en.pl", "Premier League", "2024-2025", date(2024,8,17),
    "SYNTHETIC Home", "SYNTHETIC Away", 0, 8, 2, None, 0, 1,
)


def make_report(*, digest="b"*64, rows=None):
    return {
        "source": "football-data.co.uk",
        "source_url": "https://www.football-data.co.uk/mmz4281/2425/E0.csv",
        "file_sha256": digest,
        "league": "en.pl",
        "season": "2024-2025",
        "rows": [ROW] if rows is None else rows,
    }


class MemoryLedger:
    def __init__(self):
        self.items = {}
    def get_status(self, key):
        return self.items.get(key, (None,))[0]
    def set_status(self, *, key, day, dataset, league, status, rows=None, size=None, checksum=None):
        previous = self.get_status(key)
        validate_transition(previous, status)
        self.items[key] = (status, day, dataset, league, rows, size, checksum)


def test_archives_one_season_source_as_parquet_and_checks_readback():
    store, tracker = InMemoryObjectStore(), MemoryLedger()
    receipt = archive_source_file(store, tracker, report=make_report(), permission_reference=PERMISSION)
    assert receipt.key == (
      "match-statistics/season=2024-2025/league=en.pl/date=2024-07-01/"
      "part=source-"+ "b"*64 + ".parquet")
    assert tracker.get_status(receipt.key) == ArchiveStatus.VERIFIED
    assert receipt.row_count == 1
    assert hashlib.sha256(store.get_object(receipt.key)).hexdigest() == receipt.checksum
    assert store.head_object(receipt.key).metadata["archive-key"] == receipt.key
    row = store.read_parquet(receipt.key)[0]
    assert row["home_corners"] == 0 and row["away_yellows"] is None
    assert row["source_file_sha256"] == "b"*64
    assert "odds" not in str(row).lower()


def test_second_run_reuses_immutable_verified_partition():
    store, tracker = InMemoryObjectStore(), MemoryLedger()
    initial = archive_source_file(store, tracker, report=make_report(), permission_reference=PERMISSION)
    body = store.get_object(initial.key)
    again = archive_source_file(store, tracker, report=make_report(), permission_reference=PERMISSION)
    assert again == initial
    assert store.get_object(initial.key) == body
    assert len(store.list_objects()) == 1


def test_source_revision_gets_new_key_and_keeps_old_source():
    store, tracker = InMemoryObjectStore(), MemoryLedger()
    initial = archive_source_file(store, tracker, report=make_report(), permission_reference=PERMISSION)
    revision = archive_source_file(store, tracker, report=make_report(digest="c"*64), permission_reference=PERMISSION)
    assert initial.key != revision.key
    assert len(store.list_objects()) == 2
    assert all(tracker.get_status(key.key) == ArchiveStatus.VERIFIED for key in store.list_objects())


def test_preexisting_conflicting_payload_never_overwritten():
    store, tracker = InMemoryObjectStore(), MemoryLedger()
    expected = "match-statistics/season=2024-2025/league=en.pl/date=2024-07-01/part=source-" + "b"*64 + ".parquet"
    store.put_object(expected, b"FOREIGN", content_type="application/vnd.apache.parquet")
    with pytest.raises(RuntimeError, match="Conflicting archive object"):
        archive_source_file(store, tracker, report=make_report(), permission_reference=PERMISSION)
    assert store.get_object(expected) == b"FOREIGN"
    assert tracker.get_status(expected) == ArchiveStatus.FAILED


def test_tampered_readback_is_not_success():
    class Tampered(InMemoryObjectStore):
        def get_object(self, key):
            return b"corrupt" if key.startswith("match-statistics/") else super().get_object(key)
    store, tracker = Tampered(), MemoryLedger()
    with pytest.raises(RuntimeError, match="Archive failed"):
        archive_source_file(store, tracker, report=make_report(), permission_reference=PERMISSION)
    assert len(store.list_objects()) == 1
    assert tracker.get_status(store.list_objects()[0].key) == ArchiveStatus.FAILED


@pytest.mark.parametrize("changed", [
    {"source": "untrusted.example"},
    {"league": "es.ll"},
    {"season": "2025-2026"},
    {"source_url": "https://example.invalid/data.csv"},
    {"file_sha256": "invalid"},
    {"rows": []},
])
def test_invalid_report_fails_before_r2_mutation(changed):
    store, tracker = InMemoryObjectStore(), MemoryLedger()
    with pytest.raises(ValueError):
        archive_source_file(store, tracker, report={**make_report(), **changed},
                            permission_reference=PERMISSION)
    assert store.list_objects() == []


def test_no_tracker_or_explicit_permission_fails():
    store = InMemoryObjectStore()
    with pytest.raises(ValueError, match="ARCHIVE_LEDGER_REQUIRED"):
        archive_source_file(store, None, report=make_report(), permission_reference=PERMISSION)
    with pytest.raises(ValueError, match="EXPLICIT_PERMISSION_REFERENCE_REQUIRED"):
        archive_source_file(store, MemoryLedger(), report=make_report(), permission_reference="short")
    assert not store.list_objects()


def test_raw_row_stays_unverified_stat_only():
    payload = partition_rows(make_report(), permission_reference=PERMISSION)[0]
    assert payload["match_date"] == "2024-08-17"
    assert "home_score" not in payload
    assert "result_status" not in payload
    assert "odds" not in payload
