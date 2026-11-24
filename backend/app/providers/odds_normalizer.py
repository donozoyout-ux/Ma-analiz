"""Goaloo odds normalization to ActiveOdds model format."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from pathlib import Path

import httpx


def normalize_odds_value(value: Any, hong_kong: bool = False) -> Decimal | None:
    """
    Normalize Goaloo odds value.
    
    - Returns None for empty, zero, or dash values
    - Adds 1 for Hong Kong odds (handicap, over/under)
    - Rejects odds <= 1 as invalid
    """
    if value is None or str(value).strip() in ("", "0", "-"):
        return None
    
    try:
        price = Decimal(str(value))
        if hong_kong:
            price = price + Decimal("1")
        if not price.is_finite() or price <= 1:
            return None
        return price
    except Exception:
        return None


def normalize_goaloo_odds(odds_data: list[dict]) -> tuple[list[dict], list[dict]]:
    """
    Normalize Goaloo odds to ActiveOdds-compatible format.
    
    Returns (valid_odds, rejected_odds) tuples.
    """
    valid = []
    rejected = []
    
    BOOKMAKERS = {
        "3": "Crown", "8": "Bet365", "31": "Sbobet",
        "1": "Macauslot", "4": "Ladbrokes", "12": "12bet",
        "14": "Vcbet", "17": "Mansion88", "19": "Interwetten",
        "50": "1xBet", "42": "18Bet"
    }
    
    for bookmaker in odds_data:
        cid = str(bookmaker.get('cid', ''))
        name = BOOKMAKERS.get(cid) or bookmaker.get('cn', '') or 'Unknown'
        
        for market, field, hk in [
            ('1X2', 'euro', False),
            ('AH', 'ah', True),
            ('OU', 'ou', True)
        ]:
            prices = bookmaker.get(field, {})
            if not isinstance(prices, dict):
                continue
            
            for quote_key, snapshot_type, period in [
                ('f', 'OPENING', 'PREMATCH'),
                ('l', 'LATEST', 'PREMATCH'),
                ('r', 'LIVE', 'LIVE')
            ]:
                quote = prices.get(quote_key, {})
                if not isinstance(quote, dict):
                    continue
                
                for key, selection in [('u', 'HOME'), ('g', 'DRAW'), ('d', 'AWAY')]:
                    if market == '1X2':
                        sides = [('u', 'HOME'), ('g', 'DRAW'), ('d', 'AWAY')]
                    elif market == 'AH':
                        sides = [('u', 'HOME'), ('d', 'AWAY')]
                    else:  # OU
                        sides = [('u', 'OVER'), ('d', 'UNDER')]
                    
                    for k, sel in sides:
                        if key != k:
                            continue
                        
                        raw_value = quote.get(key)
                        price = normalize_odds_value(raw_value, hong_kong=hk)
                        
                        line = quote.get('g') if market in ('AH', 'OU') else None
                        canonical_line = None
                        raw_line = str(line) if line else None
                        
                        if line and market == 'AH':
                            try:
                                parts = str(line).split('/')
                                raw_decimal = Decimal(parts[0])
                                if len(parts) > 1:
                                    other = Decimal(parts[1])
                                    raw_decimal = (raw_decimal + other) / 2
                                canonical_line = -raw_decimal
                            except:
                                pass
                        elif line and market == 'OU':
                            canonical_line = line
                        
                        if price is None:
                            rejected.append({
                                'reason': 'odds <= 1 or invalid',
                                'market': market,
                                'selection': sel,
                                'raw_value': raw_value,
                                'snapshot_type': snapshot_type,
                                'bookmaker': name
                            })
                            continue
                        
                        valid.append({
                            'provider': 'goaloo',
                            'provider_odds_id': f"{cid}:{field}:{quote_key}:{k}",
                            'provider_market_id': market,
                            'provider_bookmaker_id': cid,
                            'bookmaker': name,
                            'market': market,
                            'selection': sel,
                            'line': canonical_line,
                            'raw_line': raw_line,
                            'odds': price,
                            'captured_at': datetime.now(timezone.utc),
                            'source': 'goaloo',
                            'source_url': f"https://www.goaloo.com/football/match/oddscomp-{bookmaker.get('match_id', 'unknown')}",
                            'period': period,
                            'snapshot_type': snapshot_type,
                            'status': 'AVAILABLE',
                            'raw_value': json.dumps(quote, ensure_ascii=False, separators=(",", ":"))
                        })
    
    return valid, rejected


def fetch_and_normalize_odds(match_id: str, timeout: int = 15) -> tuple[list[dict], list[dict]]:
    """Fetch odds from Goaloo API and normalize."""
    url = "https://www.goaloo.com/ajax/soccerajax"
    referer = f"https://www.goaloo.com/football/match/oddscomp-{match_id}"
    
    headers = {
        "User-Agent": "MacAnaliz/1.0 (+https://github.com/donozoyout-ux/Ma-analiz)",
        "Referer": referer,
    }
    params = {
        "type": 14,
        "t": 1,
        "id": match_id,
        "h": 0,
        "s": -1
    }
    
    try:
        resp = httpx.get(url, params=params, headers=headers, timeout=timeout)
        if resp.status_code != 200:
            return [], [{'reason': f'HTTP {resp.status_code}'}]
        
        data = resp.json()
        if data.get('ErrCode') != 0:
            return [], [{'reason': f'Error code {data.get("ErrCode")}'}]
        
        odds_data = data.get('Data', {}).get('mixodds', [])
        return normalize_goaloo_odds(odds_data)
    except Exception as e:
        return [], [{'reason': str(e)}]