#!/usr/bin/env python3
"""Quick provider test with minimal requests."""
import os
import sys
sys.path.insert(0, '/Users/PC/Desktop/Maçanaliz/backend')

from datetime import date, datetime, timezone
import json

print("=" * 60)
print("QUICK PROVIDER TEST")
print("=" * 60)

from app.providers.openligadb import OpenLigaDBProvider

print("\n[1] Testing OpenLigaDB Provider (health check)...")
provider = OpenLigaDBProvider(requests_per_minute=10, timeout_seconds=10.0)
try:
    health = provider.health_check()
    print(f"    Status: {health.get('status')}")
    print(f"    Latency: {health.get('latency_ms', 'N/A')}ms")
except Exception as e:
    print(f"    ERROR: {e}")

print("\n[2] Fetching Bundesliga matches from 2026 season...")
try:
    matches = provider.get_matches(date(2026, 8, 29))
    print(f"    Found {len(matches)} matches for 2026-08-29")
    if matches:
        sample = matches[0]
        print(f"    Sample: {sample.get('home_team')} vs {sample.get('away_team')}")
        print(f"    Score: {sample.get('score')}")
except Exception as e:
    print(f"    ERROR: {e}")

provider.close()

print("\n" + "=" * 60)
print("TEST COMPLETE")
print("=" * 60)