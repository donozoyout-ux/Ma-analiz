"""Reviewed OpenLigaDB European club competition shortcuts, season 2026/27.

The league IDs are our stable application IDs, not numeric OpenLigaDB IDs.
They must remain distinct across leagues and are never silently mapped to
Bundesliga or another tournament. No odds are available from OpenLigaDB.
"""
from __future__ import annotations

EUROPEAN_CUPS = {
    "champions-league": {
        "name": "UEFA Champions League",
        "league_id": "uefa.cl",
        "shortcut": "ucl",
        "source_league_id": 4946,
        "season": "2026-2027",
    },
    "europa-league": {
        "name": "UEFA Europa League",
        "league_id": "uefa.el",
        "shortcut": "uel2026",
        "source_league_id": 6000,
        "season": "2026-2027",
    },
    "conference-league": {
        "name": "UEFA Conference League",
        "league_id": "uefa.ecl",
        "shortcut": "uecl2026",
        "source_league_id": 6015,
        "season": "2026-2027",
    },
}
