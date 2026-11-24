#!/usr/bin/env python3
"""
Run the pre-built Bundesliga dry-run to verify everything works.
This reads from OpenLigaDB without writing to database.
"""
import sys
sys.path.insert(0, '/Users/PC/Desktop/Maçanaliz/backend')

from datetime import date
import json
from app.jobs.bundesliga_dryrun import run_bundesliga_dryrun

print("=" * 60)
print("BUNDESLIGA DRY-RUN TEST")
print("=" * 60)

report = run_bundesliga_dryrun(
    target_day=date(2026, 8, 29),
    matchday=1
)

print(f"\nStatus: {report['status']}")
print(f"Duration: {report['duration_ms']}ms")
print(f"Matches found: {report['matches_found']}")
print(f"Existing in DB: {report['existing_in_db']}")
print(f"Verified finals: {len(report.get('verified_finals', []))}")
print(f"Conflicts: {len(report.get('conflicts', []))}")

if report.get('verified_finals'):
    print("\nFirst 3 finished matches:")
    for m in report['verified_finals'][:3]:
        print(f"  {m['home_team']} {m['score'][0]}-{m['score'][1]} {m['away_team']}")

if report.get('candidates'):
    print("\nFirst 3 match candidates:")
    for c in report['candidates'][:3]:
        print(f"  {c['home_team']} vs {c['away_team']} - {c['status']}")

print("\n" + "=" * 60)

if report['status'] == 'COMPLETED':
    print("SUCCESS: Bundesliga dry-run completed!")
    sys.exit(0)
else:
    print("FAILED: Check errors")
    if report.get('errors'):
        for e in report['errors']:
            print(f"  Error: {e}")
    sys.exit(1)