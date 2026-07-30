"""Read-only wallet valuation and persistence."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Protocol

from .models import WalletAsset, WalletSnapshot
from .repository import Repository


WRAPPED_SOL_MINT = "So11111111111111111111111111111111111111112"


class BalanceClient(Protocol):
    async def snapshot(self, address: str | None) -> WalletSnapshot | None: ...


class PriceClient(Protocol):
    async def prices(self, mints: tuple[str, ...]) -> dict[str, float | None]: ...


class WalletService:
    def __init__(
        self, repository: Repository, balances: BalanceClient, prices: PriceClient
    ) -> None:
        self.repository = repository
        self.balances = balances
        self.prices = prices

    async def sample(self, address: str | None, now: datetime) -> WalletSnapshot:
        self.repository.initialize()
        configured_address = address.strip() if address else None
        if not configured_address:
            return self._save(WalletSnapshot(None, (), None, now, "wallet address not configured"))

        balance = await self.balances.snapshot(configured_address)
        if balance is None:
            return self._save(WalletSnapshot(configured_address, (), None, now, "wallet data unavailable"))

        mints = tuple(
            dict.fromkeys(
                mint for asset in balance.assets if (mint := self._price_mint(asset)) is not None
            )
        )
        quote_by_mint = await self.prices.prices(mints)
        assets = tuple(
            sorted(
                (
                    self._value_asset(asset, quote_by_mint.get(self._price_mint(asset)))
                    for asset in balance.assets
                ),
                key=lambda asset: (
                    -(asset.usd_value if asset.usd_value is not None else float("-inf")),
                    asset.symbol,
                ),
            )
        )
        known_values = [asset.usd_value for asset in assets if asset.usd_value is not None]
        total_usd = round(sum(known_values), 2) if known_values else None
        return self._save(WalletSnapshot(configured_address, assets, total_usd, now, None))

    def _save(self, snapshot: WalletSnapshot) -> WalletSnapshot:
        self.repository.save_wallet_snapshot(snapshot)
        return snapshot

    @staticmethod
    def _value_asset(asset: WalletAsset, price_usd: float | None) -> WalletAsset:
        usd_value = (
            float(
                (Decimal(str(asset.quantity)) * Decimal(str(price_usd))).quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP
                )
            )
            if price_usd is not None
            else None
        )
        return WalletAsset(asset.mint_address, asset.symbol, asset.quantity, price_usd, usd_value)

    @staticmethod
    def _price_mint(asset: WalletAsset) -> str | None:
        if asset.mint_address is not None:
            return asset.mint_address
        return WRAPPED_SOL_MINT if asset.symbol == "SOL" else None
