"""Deterministic risk gates and quality scoring."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from .models import Candidate, Decision, TradeAdvice


@dataclass(frozen=True)
class Enrichment:
    mint_authority: bool
    freeze_authority: bool
    top10_holder_pct: float
    sampled_at: datetime


def score(candidate: Candidate, enrichment: Enrichment | None, *, now: datetime) -> Decision:
    reasons: list[str] = []
    if enrichment is None:
        return _rejected(candidate, now, "critical enrichment unavailable")
    if enrichment.mint_authority:
        return _rejected(candidate, now, "mint authority enabled")
    if enrichment.freeze_authority:
        return _rejected(candidate, now, "freeze authority enabled")
    if enrichment.top10_holder_pct > 55:
        return _rejected(candidate, now, "top 10 holders exceed 55%")
    if candidate.liquidity_usd < 10_000:
        return _rejected(candidate, now, "liquidity below $10,000")
    if now - candidate.pool_created_at > timedelta(hours=4):
        return _rejected(candidate, now, "pool older than 4 hours")
    if now - enrichment.sampled_at > timedelta(minutes=3):
        return _rejected(candidate, now, "critical enrichment stale")

    liquidity_score = 25 if candidate.liquidity_usd >= 20_000 else 15
    buy_sell_ratio = candidate.buys_m5 / max(candidate.sells_m5, 1)
    trade_score = 25 if buy_sell_ratio >= 2 and candidate.volume_m5_usd >= 2_000 else 10
    holder_score = 20 if enrichment.top10_holder_pct <= 30 else 12
    momentum_score = 15 if 10 <= candidate.price_change_m5_pct <= 80 else 5
    social_score = 0
    reasons.extend(
        (
            f"liquidity score: {liquidity_score}/25",
            f"trade score: {trade_score}/25",
            f"holder score: {holder_score}/20",
            f"momentum score: {momentum_score}/15",
            f"social score: {social_score}/15",
        )
    )
    total = liquidity_score + trade_score + holder_score + momentum_score + social_score
    status = "alerted" if total >= 85 else "watch"
    advice = _advice(candidate) if status == "alerted" else None
    return Decision(candidate.pool_address, total, status, tuple(reasons), advice, now, candidate.token_address, candidate.symbol)


def _rejected(candidate: Candidate, now: datetime, reason: str) -> Decision:
    return Decision(candidate.pool_address, 0, "rejected", (reason,), None, now, candidate.token_address, candidate.symbol)


def _advice(candidate: Candidate) -> TradeAdvice:
    return TradeAdvice(
        entry_ceiling_usd=round(candidate.price_usd * 1.05, 12),
        max_position_pct=5,
        invalidation="liquidity drops below $10,000",
        stop_loss_pct=-15,
        take_profit_pcts=(25, 50),
    )
