"""Goaloo odds fixture audit contracts (no network and no writes)."""
from decimal import Decimal

from app.providers.goaloo_odds_audit import audit_goaloo_quotes


def test_market_lines_are_not_prices_and_hk_values_are_converted():
    result = audit_goaloo_quotes([{
        "cid": 8, "cn": "Bet365",
        "euro": {"f": {"u": "1.22", "g": "7", "d": "10"}},
        "ah": {"l": {"u": "0.85", "g": "2", "d": "1.03"}},
        "ou": {"f": {"u": "0.83", "g": "4.5", "d": "0.92"}},
    }])
    assert result["counts"]["raw_numeric_field_count"] == 9
    assert result["counts"]["non_price_line_fields"] == 2
    assert result["counts"]["normalized_prices"] == 7
    prices = result["prices"]
    assert len([x for x in prices if x["selection"] == "DRAW"]) == 1
    assert next(x for x in prices if x["market"] == "AH" and x["selection"] == "HOME")["odds"] == "1.85"
    assert next(x for x in prices if x["market"] == "OU" and x["selection"] == "OVER")["odds"] == "1.83"
    assert all(x["captured_at"] is None and x["observation_time_known"] is False for x in prices)


def test_raw_one_below_threshold_not_misclassified_as_bad_hk_quote():
    result = audit_goaloo_quotes([{"cid": 3, "cn": "Crown",
                                   "ah": {"f": {"u": "0.88", "g": "1.5", "d": "0.95"}}}])
    assert [x["odds"] for x in result["prices"]] == ["1.88", "1.95"]
    assert result["counts"]["missing_or_invalid_prices"] == 0


def test_invalid_decimal_odds_excluded():
    result = audit_goaloo_quotes([{"cid": 8, "cn": "Bet365",
                                   "euro": {"f": {"u": "?", "g": "2", "d": "0"}}}])
    assert result["counts"]["normalized_prices"] == 1
    assert result["counts"]["invalid_price_values"] == 1
