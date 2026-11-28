"""Offline synthetic tests, no source page, R2 or database calls."""
from datetime import datetime, timezone

import pytest

from app.statistics.goaloo_offline import parse_statistics
from app.statistics.offline_pilot import build_pilot, valid_permission, summarize

PAGE = '''<script>var m={sId:parseInt("42"),sclassId:parseInt("8"),state:parseInt("-1")};</script>
<div id="ftstat"><ul>
<li><span class="stat-title">Corner Kicks</span><span class="stat-c">0</span><span class="stat-c">7</span></li>
<li><span class="stat-title">Yellow Cards</span><span class="stat-c">2</span><span class="stat-c">1</span></li>
<li><span class="stat-title">Shots</span><span class="stat-c">11</span><span class="stat-c">8</span></li>
</ul></div><div id="hf1stat"><ul><li><span class="stat-title">Corner Kicks</span>
<span class="stat-c">0</span><span class="stat-c">2</span></li></ul></div>'''


def fixture():
    return {"id": "fixture-1", "season": "2024-2025", "league": "de.bl1", "date": "2024-09-01",
            "home_team": "Home FC", "away_team": "Away FC"}


def observation():
    return {**fixture(), "source": "offline-synthetic", "source_match_id": "42",
            "observed_at": "2024-09-02T12:00:00Z", "is_final": True, "statistics": parse_statistics(PAGE, 42, 8)}


def test_identity_parsing_and_explicit_zero_vs_missing():
    stats = parse_statistics(PAGE, 42, 8)
    assert (stats["home_corners"], stats["away_corners"], stats["home_corners_ht"]) == (0, 7, 0)
    assert stats["home_red_cards"] is None and stats["away_red_cards"] is None
    with pytest.raises(ValueError, match="SOURCE_IDENTITY_MISMATCH"):
        parse_statistics(PAGE, 43, 8)
    with pytest.raises(ValueError, match="SOURCE_IDENTITY_MISMATCH"):
        parse_statistics(PAGE, 42, 9)


def test_pilot_fails_closed_on_missing_rights_preserves_nulls_and_reports_coverage():
    report = build_pilot([fixture()], [observation()], season="2024-2025", permission={})
    assert report["status"] == "BLOCKED_SOURCE_OR_LICENCE"
    assert report["matched"] == 1
    assert report["coverage"]["corners"] == report["coverage"]["yellow_cards"] == 1
    assert report["coverage"]["red_cards"] == 0
    assert report["matched_incomplete"] == 1
    assert report["archive"]["parquet_key"].startswith("statistics/bundesliga/season=2024-2025/")


def test_exact_identity_required_and_duplicate_rejected():
    row = observation()
    row["home_team"] = "Home United"
    report = build_pilot([fixture()], [row], season="2024-2025", permission={})
    assert report["matched"] == 0 and report["rejected"]["UNMATCHED_IDENTITY"] == 1
    with pytest.raises(ValueError, match="DUPLICATE_OR_AMBIGUOUS"):
        build_pilot([fixture()], [observation(), observation()], season="2024-2025", permission={})
    with pytest.raises(ValueError, match="AMBIGUOUS_FIXTURE"):
        build_pilot([fixture(), {**fixture(), "id": "other"}], [], season="2024-2025", permission={})


def test_invalid_and_missing_stat_and_source_time_fail_closed():
    obs = observation()
    obs["statistics"]["home_red_cards"] = -1
    with pytest.raises(ValueError, match="INVALID_STATISTIC"):
        build_pilot([fixture()], [obs], season="2024-2025", permission={})
    obs = observation()
    obs["observed_at"] = "2024-09-02T12:00:00"
    assert build_pilot([fixture()], [obs], season="2024-2025", permission={})["rejected"]["INVALID_IDENTITY_OR_TIME"] == 1


def test_permission_requires_storage_publication_and_commercial_review():
    permission = {"review_status": "APPROVED", "storage": True, "redistribution": True,
                  "commercial": True, "licence": "synthetic", "source": "offline-synthetic", "attribution": "Synthetic test",
                  "reference": "https://example.invalid/licence"}
    assert valid_permission(permission)
    assert not valid_permission({**permission, "commercial": False})
    assert not valid_permission({**permission, "reference": "http://example.invalid"})


def test_red_card_summary_preserves_missing_and_explicit_zero():
    rows = [{"home_red_cards": 0, "away_red_cards": 1},
            {"home_red_cards": None, "away_red_cards": 0}]
    result = summarize(rows, "red_cards")
    assert result == {"metric": "red_cards", "sample_size": 1, "missing": 1,
                      "home_average": 0, "away_average": 1}


def test_cli_reads_only_local_files_and_reports_unapproved_source(tmp_path):
    import json
    import subprocess
    import sys

    files = {}
    for key, value in (("fixtures", [fixture()]), ("observations", [observation()]), ("permission", {})):
        path = tmp_path / (key + ".json")
        path.write_text(json.dumps(value), encoding="utf8")
        files[key] = str(path)
    result = subprocess.run([sys.executable, "-m", "app.jobs.bundesliga_statistics_pilot",
                             "--fixtures", files["fixtures"], "--observations", files["observations"],
                             "--permission", files["permission"], "--season", "2024-2025"],
                            capture_output=True, text=True)
    assert result.returncode == 2
    report = json.loads(result.stdout)
    assert report["status"] == "BLOCKED_SOURCE_OR_LICENCE" and report["matched"] == 1


def test_nonfinal_observation_is_not_archived():
    with pytest.raises(ValueError, match="SOURCE_NOT_FINAL"):
        parse_statistics(PAGE.replace('state:parseInt("-1")', 'state:parseInt("1")'), 42, 8)
    obs = {**observation(), "is_final": False}
    report = build_pilot([fixture()], [obs], season="2024-2025", permission={})
    assert report["matched"] == 0 and report["rejected"]["STATISTICS_NOT_FINAL"] == 1
    assert report["archive"] is None
