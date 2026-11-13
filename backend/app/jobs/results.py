"""Verified-result settlement and aggregate calculations."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ActiveMatch, Coupon, CouponItem, DailyMetric, MatchResult, Prediction


def prediction_outcome(market: str, selection: str, home: int, away: int) -> bool | None:
    market, selection = market.strip().upper().replace(" ", "_"), selection.strip().upper().replace(" ", "_")
    if market in {"1X2", "MATCH_WINNER", "FULL_TIME_RESULT"}:
        actual = "HOME" if home > away else "AWAY" if away > home else "DRAW"
        return selection in {actual, {"HOME": "1", "DRAW": "X", "AWAY": "2"}[actual]}
    if market.startswith("OVER_UNDER") or market in {"TOTAL_GOALS", "GOALS_OU"}:
        total = home + away
        if selection.startswith("OVER_"):
            try: return total > Decimal(selection.removeprefix("OVER_"))
            except Exception: return None
        if selection.startswith("UNDER_"):
            try: return total < Decimal(selection.removeprefix("UNDER_"))
            except Exception: return None
        return None
    if market in {"BTTS", "BOTH_TEAMS_TO_SCORE"}:
        actual = "YES" if home > 0 and away > 0 else "NO"
        return selection == actual
    return None


def settle_verified_results(session: Session, *, day: date) -> dict[str, int]:
    start = datetime.combine(day, time.min, tzinfo=timezone.utc)
    end = start + timedelta(days=1)
    matches = session.scalars(select(ActiveMatch).where(ActiveMatch.start_time >= start, ActiveMatch.start_time < end)).all()
    settled = skipped = 0
    for match in matches:
        result = session.scalar(select(MatchResult).where(
            MatchResult.match_id == match.id, MatchResult.status == "FINAL",
            MatchResult.verified_at.is_not(None),
        ))
        if result is None:
            continue
        predictions = session.scalars(select(Prediction).where(
            Prediction.match_id == match.id, Prediction.status == "PENDING"
        ).with_for_update()).all()
        for prediction in predictions:
            won = prediction_outcome(prediction.market, prediction.selection, result.home_score, result.away_score)
            if won is None:
                skipped += 1
                continue
            prediction.status = "WON" if won else "LOST"
            prediction.paper_return = (prediction.paper_stake * prediction.decimal_odds if won and prediction.decimal_odds
                                        else Decimal("0") if not won else None)
            prediction.settled_at = result.verified_at
            settled += 1
            for item in session.scalars(select(CouponItem).where(
                CouponItem.prediction_id == prediction.id, CouponItem.status == "PENDING"
            ).with_for_update()).all():
                item.status, item.settled_at = prediction.status, result.verified_at

    coupons = session.scalars(select(Coupon).where(Coupon.status == "PAPER_PENDING").with_for_update()).all()
    coupons_settled = 0
    for coupon in coupons:
        items = session.scalars(select(CouponItem).where(CouponItem.coupon_id == coupon.id)).all()
        if not items or any(item.status == "PENDING" for item in items):
            continue
        if any(item.status == "LOST" for item in items):
            coupon.status = "PAPER_LOST"
            coupon.paper_profit = -coupon.paper_stake
        elif all(item.status == "WON" for item in items):
            coupon.status = "PAPER_WON"
            coupon.paper_profit = coupon.paper_stake * (coupon.total_odds - Decimal("1"))
        else:
            coupon.status = "PAPER_VOID"
            coupon.paper_profit = Decimal("0")
        coupon.settled_at = datetime.now(timezone.utc)
        coupons_settled += 1
    session.commit()
    return {"predictions_settled": settled, "predictions_unscored": skipped, "coupons_settled": coupons_settled}


def aggregate_daily_metrics(session: Session, *, day: date, postgres_bytes: int | None = None,
                            r2_bytes: int | None = None) -> DailyMetric:
    start = datetime.combine(day, time.min, tzinfo=timezone.utc)
    end = start + timedelta(days=1)
    matches = session.scalars(select(ActiveMatch).where(ActiveMatch.start_time >= start, ActiveMatch.start_time < end)).all()
    predictions = session.scalars(select(Prediction).where(Prediction.created_at >= start, Prediction.created_at < end)).all()
    coupons = session.scalars(select(Coupon).where(Coupon.created_at >= start, Coupon.created_at < end)).all()
    settled = [p for p in predictions if p.status in {"WON", "LOST"}]
    wins = sum(p.status == "WON" for p in settled)
    losses = sum(p.status == "LOST" for p in settled)
    settled_coupons = [c for c in coupons if c.status in {"PAPER_WON", "PAPER_LOST"}]
    coupon_wins = sum(c.status == "PAPER_WON" for c in settled_coupons)
    coupon_losses = sum(c.status == "PAPER_LOST" for c in settled_coupons)
    profit_values = [(p.paper_return - p.paper_stake) for p in settled if p.paper_return is not None]
    stake_values = [p.paper_stake for p in settled if p.decimal_odds is not None]
    metric = session.get(DailyMetric, day)
    if metric is None:
        metric = DailyMetric(date=day)
        session.add(metric)
    metric.total_matches = len(matches)
    metric.analyzed_matches = len({p.match_id for p in predictions})
    metric.predictions = len(predictions)
    metric.predictions_pending = sum(p.status == "PENDING" for p in predictions)
    metric.wins, metric.losses = wins, losses
    metric.hit_rate = Decimal(wins) / Decimal(wins + losses) if wins + losses else None
    metric.coupons, metric.coupon_wins, metric.coupon_losses = len(coupons), coupon_wins, coupon_losses
    metric.paper_pl = sum(profit_values, Decimal("0")) if profit_values else None
    metric.paper_stake = sum(stake_values, Decimal("0")) if stake_values else None
    metric.postgres_bytes, metric.r2_bytes = postgres_bytes, r2_bytes
    session.commit()
    return metric
