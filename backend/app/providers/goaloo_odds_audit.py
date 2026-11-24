"""Read-only audit for Goaloo mixodds fields.

Raw numeric field counts must NOT be interpreted as decimal quote counts:
AH/OU 'g' encodes handicap/goal line, while 'u' and 'd' encode prices.
Uses the existing GoalooProvider._price (Hong Kong -> decimal +1).
"""
from __future__ import annotations

from collections import Counter
from decimal import Decimal
from typing import Any

from app.providers.goaloo import GoalooProvider


def audit_goaloo_quotes(rows: list[dict[str, Any]]) -> dict:
    counts = Counter()
    normalized = []
    rejected = []
    for bookmaker in rows:
        if not isinstance(bookmaker, dict):
            counts["invalid_bookmaker_rows"] += 1
            continue
        cid = str(bookmaker.get("cid", ""))
        name = str(bookmaker.get("cn", "")).strip()
        if not cid or not name:
            counts["invalid_bookmaker_rows"] += 1
            continue
        for market, field, sides, hk in (
            ("1X2", "euro", (("u", "HOME"), ("g", "DRAW"), ("d", "AWAY")), False),
            ("AH", "ah", (("u", "HOME"), ("d", "AWAY")), True),
            ("OU", "ou", (("u", "OVER"), ("d", "UNDER")), True),
        ):
            prices = bookmaker.get(field)
            if not isinstance(prices, dict):
                continue
            for snapshot in ("f", "l", "r"):
                quote = prices.get(snapshot)
                if not isinstance(quote, dict):
                    continue
                counts["raw_numeric_field_count"] += sum(
                    str(v) not in ("", "-", "0") and _is_number(v)
                    for v in quote.values()
                )
                if market in ("AH", "OU") and quote.get("g") is not None:
                    counts["non_price_line_fields"] += 1
                for side, selection in sides:
                    try:
                        price = GoalooProvider._price(quote.get(side), hong_kong=hk)
                    except Exception:
                        counts["invalid_price_values"] += 1
                        rejected.append({"bookmaker": name, "market": market, "snapshot": snapshot,
                                         "selection": selection, "reason": "INVALID_PRICE_FORMAT"})
                        continue
                    if price is None:
                        counts["missing_or_invalid_prices"] += 1
                        continue
                    normalized.append({
                        "provider": "goaloo", "provider_bookmaker_id": cid, "bookmaker": name,
                        "market": market, "selection": selection, "snapshot_type": {
                            "f": "OPENING", "l": "LATEST", "r": "SOURCE_LIVE_LABEL"}[snapshot],
                        "odds": str(price), "raw_price": str(quote[side]),
                        "raw_line": str(quote.get("g")) if market in ("AH", "OU") else None,
                        "captured_at": None,
                        # A static JSON export has no reliable per-quote source timestamp.
                        "observation_time_known": False,
                    })
    for key in ("raw_numeric_field_count", "non_price_line_fields", "invalid_price_values",
                "missing_or_invalid_prices", "invalid_bookmaker_rows"):
        counts.setdefault(key, 0)
    counts["normalized_prices"] = len(normalized)
    counts["rejected_rows"] = len(rejected)
    counts["bookmakers"] = len({row["provider_bookmaker_id"] for row in normalized})
    return {"counts": dict(counts), "prices": normalized, "rejections": rejected}


def _is_number(value: Any) -> bool:
    try:
        return Decimal(str(value)).is_finite()
    except Exception:
        return False
