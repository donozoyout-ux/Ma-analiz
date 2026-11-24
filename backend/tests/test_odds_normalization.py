"""Tests for odds normalization."""
import json
from decimal import Decimal
from pathlib import Path

import pytest

from app.providers.odds_normalizer import (
    normalize_odds_value,
    normalize_goaloo_odds,
)


@pytest.fixture
def raw_odds_data():
    """Load raw Goaloo odds test data."""
    path = Path(__file__).parent.parent.parent / 'goaloo_match_3018786_odds.json'
    if path.exists():
        return json.loads(path.read_text())
    return []


def test_normalize_odds_value_valid():
    """Test valid odds normalization."""
    assert normalize_odds_value(1.5, False) == Decimal('1.5')
    assert normalize_odds_value(2.0, False) == Decimal('2.0')
    assert normalize_odds_value(1.95, False) == Decimal('1.95')


def test_normalize_odds_value_hong_kong():
    """Test Hong Kong odds (handicap, over/under)."""
    assert normalize_odds_value(Decimal('0.25'), True) == Decimal('1.25')
    assert normalize_odds_value(Decimal('-0.5'), True) == Decimal('0.5')


def test_normalize_odds_value_invalid():
    """Test invalid odds rejection."""
    assert normalize_odds_value(None) is None
    assert normalize_odds_value("") is None
    assert normalize_odds_value("0") is None
    assert normalize_odds_value("-") is None
    assert normalize_odds_value(Decimal("0.5")) is None
    assert normalize_odds_value(Decimal("1.0")) is None
    assert normalize_odds_value(Decimal("0.99")) is None


def test_normalize_goaloo_odds_counts(raw_odds_data):
    """Verify normalization counts: 310 raw -> 246 valid, 64 rejected."""
    valid, rejected = normalize_goaloo_odds(raw_odds_data)
    
    # Note: The total raw odds per bookmaker varies by market
    # Expected: 246 valid, with some rejected for odds < 1
    assert len(valid) == 246, f"Expected 246 valid, got {len(valid)}"
    
    # Rejected for odds < 1
    invalid_for_odds = [r for r in rejected if r.get('reason') == 'odds <= 1 or invalid']
    assert len(invalid_for_odds) == 58, f"Expected 58 rejected, got {len(invalid_for_odds)}"


def test_normalize_odds_fields():
    """Verify normalized odds have required fields."""
    sample_data = [{
        'cid': '8',
        'cn': 'Bet365',
        'euro': {
            'f': {'u': '1.5', 'g': '3.0', 'd': '4.0'},
            'l': {'u': '1.4', 'g': '3.1', 'd': '3.9'},
            'r': {'u': '2.0', 'g': '4.5', 'd': '6.0'}
        }
    }]
    
    valid, rejected = normalize_goaloo_odds(sample_data)
    
    assert len(valid) > 0
    for o in valid:
        assert 'provider' in o
        assert 'match_id' in o or 'provider_odds_id' in o
        assert 'bookmaker' in o
        assert 'market' in o
        assert 'selection' in o
        assert 'odds' in o
        assert o['odds'] >= 1


def test_odds_bookmaker_mapping():
    """Verify bookmaker name mapping."""
    sample_data = [{
        'cid': '8',
        'cn': 'Bet365',
        'euro': {'f': {'u': '1.5', 'g': '3.0', 'd': '4.0'}}
    }]
    
    valid, _ = normalize_goaloo_odds(sample_data)
    assert valid[0]['bookmaker'] == 'Bet365'


def test_odds_1x2_market():
    """Test 1X2 market normalization."""
    sample_data = [{
        'cid': '8',
        'cn': 'Bet365',
        'euro': {'f': {'u': '1.5', 'g': '3.0', 'd': '4.0'}}
    }]
    
    valid, _ = normalize_goaloo_odds(sample_data)
    
    markets = {o['market'] for o in valid}
    assert '1X2' in markets


def test_odds_ah_market():
    """Test Asian Handicap market normalization."""
    sample_data = [{
        'cid': '8',
        'cn': 'Bet365',
        'ah': {'f': {'u': '0.85', 'g': '0.5', 'd': '1.05'}}
    }]
    
    valid, _ = normalize_goaloo_odds(sample_data)
    
    ah_odds = [o for o in valid if o['market'] == 'AH']
    assert len(ah_odds) > 0


def test_odds_ou_market():
    """Test Over/Under market normalization."""
    sample_data = [{
        'cid': '8',
        'cn': 'Bet365',
        'ou': {'f': {'u': '1.85', 'g': '2.5', 'd': '1.95'}}
    }]
    
    valid, _ = normalize_goaloo_odds(sample_data)
    
    ou_odds = [o for o in valid if o['market'] == 'OU']
    assert len(ou_odds) > 0


def test_raw_value_preserved():
    """Verify raw_value is preserved in output."""
    sample_data = [{
        'cid': '8',
        'cn': 'Bet365',
        'euro': {'f': {'u': '1.5', 'g': '3.0', 'd': '4.0'}}
    }]
    
    valid, _ = normalize_goaloo_odds(sample_data)
    
    for o in valid:
        assert o.get('raw_value') is not None
        assert 'u' in o['raw_value'] or 'g' in o['raw_value']