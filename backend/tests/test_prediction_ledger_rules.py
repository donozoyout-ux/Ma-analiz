from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from app.jobs.prediction_ledger_rules import PublicationCandidate, hit_rate, outcome_1x2, validate_candidate

NOW = datetime(2026, 11, 28, 12, tzinfo=timezone.utc)
BASE = PublicationCandidate("match-1", NOW + timedelta(hours=2), NOW, "1X2", "1", Decimal("0.5"), "v1", "a" * 64)


def test_prematch_and_timezone():
    validate_candidate(BASE, trusted_now=NOW)
    with pytest.raises(ValueError, match="PUBLICATION_NOT_BEFORE"):
        validate_candidate(replace(BASE, published_at=BASE.kickoff_at), trusted_now=BASE.kickoff_at)
    with pytest.raises(ValueError, match="TIMEZONE_REQUIRED"):
        validate_candidate(replace(BASE, published_at=NOW.replace(tzinfo=None)), trusted_now=NOW)
    with pytest.raises(ValueError, match="PUBLICATION_NOT_BEFORE"):
        validate_candidate(replace(BASE, published_at=NOW + timedelta(seconds=1)), trusted_now=NOW)


def test_odds_require_source_and_prepublication_capture():
    validate_candidate(replace(BASE, odds=Decimal("2"), odds_captured_at=NOW, odds_source_ref="licensed-source"), trusted_now=NOW)
    with pytest.raises(ValueError, match="LATE_ODDS"):
        validate_candidate(replace(BASE, odds=Decimal("2"), odds_captured_at=NOW + timedelta(seconds=1), odds_source_ref="x"), trusted_now=NOW)
    with pytest.raises(ValueError, match="INCOMPLETE_ODDS"):
        validate_candidate(replace(BASE, odds=Decimal("2")), trusted_now=NOW)


def test_result_requires_verified_final_and_known_selection():
    data = dict(market="1X2", selection="X", result_status="FINAL", home=0, away=0,
                verified_at=NOW + timedelta(hours=3), result_source="verified-source", kickoff_at=BASE.kickoff_at,
                observed_now=NOW + timedelta(hours=4))
    assert outcome_1x2(**data) == "WON"
    assert outcome_1x2(**{**data, "selection": "1"}) == "LOST"
    for changes in ({"result_status": "POSTPONED"}, {"verified_at": None}, {"result_source": None},
                    {"home": None}, {"home": True}, {"selection": "OTHER"}, {"market": "OVER_UNDER"},
                    {"verified_at": NOW}):
        assert outcome_1x2(**{**data, **changes}) == "UNSCORED"


def test_rate_excludes_pending_void_and_unknown():
    assert hit_rate(["WON", "LOST", "PENDING", "VOID", "UNSCORED"]) == (1, 1, Decimal("0.5"))
    assert hit_rate(["PENDING", "VOID"]) == (0, 0, None)
