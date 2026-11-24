"""Tests for cross-provider match mapping."""
import json
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from app.providers.match_mapper import (
    GoalooOpenLigaDBMapper,
    MatchMapping,
    save_mapping_report,
)


@pytest.fixture
def openliga_data():
    """Load OpenLigaDB test data."""
    path = Path(__file__).parent.parent.parent / 'openliga_full_2026.json'
    if path.exists():
        return json.loads(path.read_text(encoding='utf-8'))
    return []


@pytest.fixture
def goaloo_data():
    """Load Goaloo test data."""
    path = Path(__file__).parent.parent.parent / 'goaloo_2026_2027.json'
    if path.exists():
        return json.loads(path.read_text(encoding='utf-8'))
    return {}


def test_match_mapper_53_matched(openliga_data, goaloo_data):
    """Verify 53 matches are correctly matched."""
    mapper = GoalooOpenLigaDBMapper(openliga_data, goaloo_data)
    results = mapper.map_all()
    
    matched = [r for r in results if r.status == 'MATCHED']
    assert len(matched) == 53, f"Expected 53 matched, got {len(matched)}"
    
    # Verify sample match
    sample = matched[0]
    assert sample.openligadb_id is not None
    assert sample.confidence == 100


def test_match_mapper_248_needs_review(openliga_data, goaloo_data):
    """Verify 248 matches need review."""
    mapper = GoalooOpenLigaDBMapper(openliga_data, goaloo_data)
    results = mapper.map_all()
    
    needs_review = [r for r in results if r.status == 'NEEDS_REVIEW']
    assert len(needs_review) == 248, f"Expected 248 review, got {len(needs_review)}"


def test_match_mapper_5_no_match(openliga_data, goaloo_data):
    """Verify 5 matches have no match."""
    mapper = GoalooOpenLigaDBMapper(openliga_data, goaloo_data)
    results = mapper.map_all()
    
    no_match = [r for r in results if r.status == 'NO_MATCH']
    assert len(no_match) == 5, f"Expected 5 no match, got {len(no_match)}"


def test_match_mapper_total_counts(openliga_data, goaloo_data):
    """Verify total match count."""
    mapper = GoalooOpenLigaDBMapper(openliga_data, goaloo_data)
    results = mapper.map_all()
    
    total = len(results)
    counts = save_mapping_report(results)
    
    assert counts['MATCHED'] == 53
    assert counts['NEEDS_REVIEW'] == 248
    assert counts['NO_MATCH'] == 5
    assert total == 306


def test_match_evidence(openliga_data, goaloo_data):
    """Verify matched matches have evidence."""
    mapper = GoalooOpenLigaDBMapper(openliga_data, goaloo_data)
    results = mapper.map_all()
    
    matched = [r for r in results if r.status == 'MATCHED']
    for m in matched[:3]:
        assert 'evidence' in m.__dict__
        assert 'key' in m.evidence
        assert 'openligadb_match_id' in m.evidence


def test_save_mapping_report():
    """Verify report saving."""
    mappings = [
        MatchMapping('1', '100', 'Team A', 'Team B', '2026-01-01', 'MATCHED', 100, {}),
        MatchMapping('2', None, 'Team C', 'Team D', '2026-01-02', 'NEEDS_REVIEW', 80, {}),
        MatchMapping('3', None, 'Team E', 'Team F', '2026-01-03', 'NO_MATCH', 0, {}),
    ]
    
    counts = save_mapping_report(mappings, '/tmp/test_mapping_report.json')
    
    assert counts['MATCHED'] == 1
    assert counts['NEEDS_REVIEW'] == 1
    assert counts['NO_MATCH'] == 1
    
    report_path = Path('/tmp/test_mapping_report.json')
    if report_path.exists():
        report = json.loads(report_path.read_text())
        assert 'summary' in report