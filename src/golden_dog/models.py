"""Immutable data exchanged by the signal pipeline."""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal


@dataclass(frozen=True)
class Candidate:
    pool_address: str
    token_address: str
    symbol: str
    discovered_at: datetime
    pool_created_at: datetime
    liquidity_usd: float
    volume_m5_usd: float
    buys_m5: int
    sells_m5: int
    price_change_m5_pct: float
    price_usd: float = 0.0


@dataclass(frozen=True)
class TradeAdvice:
    entry_ceiling_usd: float
    max_position_pct: int
    invalidation: str
    stop_loss_pct: int
    take_profit_pcts: tuple[int, int]


@dataclass(frozen=True)
class Decision:
    pool_address: str
    score: int
    status: Literal["rejected", "watch", "alerted"]
    reasons: tuple[str, ...]
    advice: TradeAdvice | None
    observed_at: datetime
