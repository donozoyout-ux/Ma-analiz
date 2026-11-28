"""Manual probe is opt-in and emits metadata only; all fixtures are synthetic."""
from datetime import datetime, timezone
import json
import sys

import pytest
from app.providers.cornerflick_open_data import CornerflickDocument
from app.providers.cornerflick_probe import describe_document, main


def fake_document():
    return CornerflickDocument(
        dataset="match_record",
        competition="bundesliga",
        source_url="https://cornerflick.com/football/bundesliga/matches/synthetic-match/data.json",
        retrieved_at=datetime(2026, 11, 28, tzinfo=timezone.utc),
        content_sha256="a" * 64,
        attribution="Data by Cornerflick",
        licence="CC BY 4.0",
        payload={
            "meta": {"last_updated_at": "do-not-print-value"},
            "events": [{"player": "DO_NOT_PRINT_NAME", "minute": 90}],
            "result": {"home_score": 0, "away_score": None},
        },
    )


def test_probe_reports_only_shape_and_not_match_player_values():
    data = describe_document(fake_document())
    output = json.dumps(data)
    assert "$.result.home_score" in output
    assert data["source_result_not_verified"] is True
    assert data["writes_or_ingestion"] is False
    assert "DO_NOT_PRINT_NAME" not in output
    assert "do-not-print-value" not in output


@pytest.mark.parametrize("argv", [
    ["cornerflick_probe", "--season"],
    ["cornerflick_probe", "--match-slug", "synthetic-match", "--allow-one-request"],
    ["cornerflick_probe", "--season", "--acknowledge-source-terms"],
])
def test_cli_denies_network_without_two_independent_confirmations(monkeypatch, argv):
    monkeypatch.setattr(sys, "argv", argv)
    with pytest.raises(SystemExit) as result:
        main()
    assert result.value.code == 2


def test_cli_one_request_stub_without_actual_network(monkeypatch, capsys):
    import app.providers.cornerflick_probe as probe
    class Stub:
        def __init__(self, **kwargs):
            assert kwargs == {"enabled": True}
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def season_results(self):
            return fake_document()
    monkeypatch.setattr(probe, "CornerflickOpenData", Stub)
    monkeypatch.setattr(sys, "argv", [
        "cornerflick_probe", "--season", "--allow-one-request", "--acknowledge-source-terms"
    ])
    assert main() == 0
    output = capsys.readouterr().out
    assert "$.events" in output
    assert "DO_NOT_PRINT_NAME" not in output
