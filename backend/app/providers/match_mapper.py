"""Cross-provider match mapper for Goaloo and OpenLigaDB Bundesliga data."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Any
from pathlib import Path

import json


@dataclass
class MatchMapping:
    """Result of a cross-provider match mapping."""
    goaloo_id: str
    openligadb_id: str | None
    home_team: str
    away_team: str
    match_date: str
    status: str  # 'MATCHED', 'NEEDS_REVIEW', 'NO_MATCH'
    confidence: int
    evidence: dict[str, Any]


class GoalooOpenLigaDBMapper:
    """Maps Goaloo match IDs to OpenLigaDB matches using team names and dates."""
    
    TEAM_ALIASES = {
        'bayern munich': 'fc bayern munchen',
        'borussia dortmund': 'borussia dortmund',
        'hamburger sv': 'hamburger sv',
        'vfl wolfsburg': 'vfl wolfsburg',
        'eintracht frankfurt': 'eintracht frankfurt',
        'bayer leverkusen': 'bayer leverkusen',
        'rb leipzig': 'rb leipzig',
        'fc koln': '1. fc köln',
        'sc freiburg': 'sportfreund freiburg',
        'vfb stuttgart': 'vfb stuttgart',
        'fortuna düsseldorf': 'fortuna düsseldorf',
        'mainz 05': '1. fsv mainz 05',
        'sc paderborn': 'sc paderborn 07',
    }
    
    def __init__(self, openliga_data: list[dict], goaloo_data: dict):
        self.openliga_index = self._build_openliga_index(openliga_data)
        self.goaloo_data = goaloo_data
        self.teams = self._build_team_lookup()
    
    def _build_openliga_index(self, matches: list[dict]) -> dict[tuple, dict]:
        """Build index of OpenLigaDB matches keyed by (home, away, date)."""
        index = {}
        for m in matches:
            home = re.sub(r'[^\w\s]', '', m.get('team1', {}).get('teamName', '')).lower()
            away = re.sub(r'[^\w\s]', '', m.get('team2', {}).get('teamName', '')).lower()
            dt = m.get('matchDateTimeUTC', '')
            date_str = dt[:10] if dt else ''
            key = (home, away, date_str)
            index[key] = m
        return index
    
    def _build_team_lookup(self) -> dict[str, str]:
        """Build Goaloo team ID to normalized name lookup."""
        teams = {}
        team_info = self.goaloo_data.get('TeamInfo', [])
        for t in team_info:
            if isinstance(t, list) and len(t) >= 2:
                team_id = str(t[0])
                name = str(t[1]).lower()
                teams[team_id] = name
        return teams
    
    def _normalize_team(self, raw_name: str) -> str:
        """Normalize team name using aliases."""
        normalized = raw_name.lower()
        for alias, canonical in self.TEAM_ALIASES.items():
            if alias in normalized or normalized in alias:
                return canonical
        return normalized
    
    def _parse_goaloo_date(self, kickoff_str: str) -> str:
        """Parse Goaloo kickoff time (UTC+8) to ISO date."""
        kickoff = datetime.strptime(kickoff_str, "%Y-%m-%d %H:%M")
        kickoff_utc = kickoff.replace(tzinfo=timezone(timedelta(hours=8))).astimezone(timezone.utc)
        return kickoff_utc.strftime("%Y-%m-%d")
    
    def map_all(self) -> list[MatchMapping]:
        """Map all Goaloo matches to OpenLigaDB."""
        results = []
        schedule = self.goaloo_data.get('ScheduleList', {})
        
        for round_data in schedule.values():
            if not isinstance(round_data, list):
                continue
            for row in round_data:
                if not isinstance(row, list) or len(row) < 8:
                    continue
                mapping = self._map_match(row)
                results.append(mapping)
        
        return results
    
    def _map_match(self, row: list) -> MatchMapping:
        """Map a single Goaloo match row."""
        goaloo_id = str(row[0])
        kickoff_str = str(row[3])
        home_id = str(row[4])
        away_id = str(row[5])
        score = str(row[6])
        
        home_raw = self.teams.get(home_id, '')
        away_raw = self.teams.get(away_id, '')
        
        home = self._normalize_team(home_raw)
        away = self._normalize_team(away_raw)
        
        try:
            date_str = self._parse_goaloo_date(kickoff_str)
        except:
            date_str = kickoff_str[:10]
        
        key = (home, away, date_str)
        
        if key in self.openliga_index:
            ol_match = self.openliga_index[key]
            return MatchMapping(
                goaloo_id=goaloo_id,
                openligadb_id=str(ol_match.get('matchID', '')),
                home_team=home_raw.title(),
                away_team=away_raw.title(),
                match_date=date_str,
                status='MATCHED',
                confidence=100,
                evidence={'key': key, 'openligadb_match_id': ol_match.get('matchID')}
            )
        
        # Try partial match for review
        best_match = self._find_partial_match(home, away, date_str)
        if best_match:
            return MatchMapping(
                goaloo_id=goaloo_id,
                openligadb_id=None,
                home_team=home_raw.title(),
                away_team=away_raw.title(),
                match_date=date_str,
                status='NEEDS_REVIEW',
                confidence=best_match['confidence'],
                evidence=best_match['evidence']
            )
        
        return MatchMapping(
            goaloo_id=goaloo_id,
            openligadb_id=None,
            home_team=home_raw.title(),
            away_team=away_raw.title(),
            match_date=date_str,
            status='NO_MATCH',
            confidence=0,
            evidence={'key': key, 'attempted': True}
        )
    
    def _find_partial_match(self, home: str, away: str, date_str: str) -> dict | None:
        """Find best partial match for review queue."""
        best = None
        best_score = 0
        
        for key, match in self.openliga_index.items():
            ol_home, ol_away, ol_date = key
            score = 0
            
            if home == ol_home or ol_home in home or home in ol_home:
                score += 50
            if away == ol_away or ol_away in away or away in ol_away:
                score += 50
            if date_str == ol_date:
                score += 100
            
            if score > best_score and score >= 100:
                best_score = score
                best = {
                    'confidence': score,
                    'evidence': {
                        'key': key,
                        'openligadb_match_id': match.get('matchID'),
                        'score': score
                    }
                }
        
        return best


def save_mapping_report(mappings: list[MatchMapping], output_path: str = None) -> dict[str, int]:
    """Save mapping report to JSON and return counts."""
    by_status = {'MATCHED': 0, 'NEEDS_REVIEW': 0, 'NO_MATCH': 0}
    matched = []
    needs_review = []
    no_match = []
    
    for m in mappings:
        by_status[m.status] += 1
        if m.status == 'MATCHED':
            matched.append(m.__dict__)
        elif m.status == 'NEEDS_REVIEW':
            needs_review.append(m.__dict__)
        else:
            no_match.append(m.__dict__)
    
    report = {
        'summary': by_status,
        'matched': matched,
        'needs_review': needs_review,
        'no_match': no_match
    }
    
    if output_path:
        Path(output_path).write_text(json.dumps(report, indent=2, default=str), encoding='utf-8')
    
    return by_status