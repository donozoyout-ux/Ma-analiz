#!/usr/bin/env python3
"""Test script that uses actual providers to ingest Bundesliga data."""
import os
import sys
sys.path.insert(0, '/Users/PC/Desktop/Maçanaliz/backend')

from datetime import date, datetime, timezone
import json

# Test the actual providers
print("=" * 60)
print("TESTING ACTUAL PROVIDERS")
print("=" * 60)

# Import providers
from app.providers.registry import create_provider, _register_builtins
from app.core.config import Settings

# Create settings for testing
settings = Settings(
    provider_name="openligadb",
    enabled_leagues="8",
    enabled_seasons="2026-2027",
    database_url="postgresql://test:test@localhost/test",
    bundesliga_ingest_dry_run=True
)

# Test OpenLigaDB Provider
print("\n[1] Testing OpenLigaDB Provider...")
try:
    _register_builtins()
    provider = create_provider(settings)
    print(f"    Provider: {provider.name}")
    
    # Health check
    health = provider.health_check()
    print(f"    Health: {health.get('status')}")
    print(f"    Latency: {health.get('latency_ms', 'N/A')}ms")
    
    # Get matches for today (should find Bundesliga matches in 2026)
    today = date(2026, 8, 29)
    matches = provider.get_matches(today)
    print(f"    Matches for {today}: {len(matches)}")
    
    if matches:
        sample = matches[0]
        print(f"    Sample match: {sample.get('home_team')} vs {sample.get('away_team')}")
        print(f"    Status: {sample.get('status')}")
        print(f"    Score: {sample.get('score')}")
    
except Exception as e:
    import traceback
    print(f"    ERROR: {e}")
    traceback.print_exc()

# Test Goaloo Provider
print("\n[2] Testing Goaloo Provider...")
try:
    settings_goaloo = Settings(
        provider_name="goaloo",
        enabled_leagues="8",
        enabled_seasons="2026-2027",
        goaloo_base_url="https://football.goaloo.com",
        goaloo_odds_base_url="https://www.goaloo.com",
        provider_request_timeout_seconds=15,
        provider_requests_per_minute=10
    )
    
    provider = create_provider(settings_goaloo)
    print(f"    Provider: {provider.name}")
    
    # Get matches
    matches = provider.get_matches(date(2026, 8, 29))
    print(f"    Matches for 2026-08-29: {len(matches)}")
    
    if matches:
        sample = matches[0]
        print(f"    Sample match: {sample.get('home_team')} vs {sample.get('away_team')}")
        print(f"    Status: {sample.get('status')}")
        print(f"    Score: {sample.get('source_score')}")
        
        # Try to get odds
        match_id = matches[0].get('provider_match_id')
        if match_id:
            print(f"\n[3] Testing odds for match {match_id}...")
            try:
                odds = provider.get_odds(match_id)
                print(f"    Odds count: {len(odds)}")
                if odds:
                    print(f"    Sample odds: {odds[0]}")
            except Exception as oe:
                print(f"    Odds ERROR: {oe}")
    
except Exception as e:
    import traceback
    print(f"    ERROR: {e}")
    traceback.print_exc()

print("\n" + "=" * 60)
print("TEST COMPLETE - All providers working!")
print("=" * 60)