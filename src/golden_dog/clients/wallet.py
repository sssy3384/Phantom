"""Read-only public Solana wallet balance client."""

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

import httpx

from ..models import WalletAsset, WalletSnapshot


class WalletClient:
    das_page_limit = 1000

    def __init__(
        self,
        http_client: httpx.AsyncClient,
        api_key: str | None,
        max_das_pages: int = 10,
    ) -> None:
        self.http_client = http_client
        self.api_key = api_key
        self.max_das_pages = max_das_pages

    @property
    def url(self) -> str:
        return f"https://mainnet.helius-rpc.com/?api-key={self.api_key}"

    async def snapshot(self, address: str | None) -> WalletSnapshot | None:
        if not self.api_key or not address:
            return None
        try:
            balance = await self._rpc("getBalance", [address])
            token_assets, partial = await self._das_assets(address)
            assets = (self._sol_asset(balance), *token_assets)
            return WalletSnapshot(address, assets, None, datetime.now(UTC), None, partial)
        except (httpx.HTTPError, KeyError, TypeError, ValueError):
            return None

    async def _das_assets(self, address: str) -> tuple[tuple[WalletAsset, ...], bool]:
        assets: dict[str, WalletAsset] = {}
        for page in range(1, self.max_das_pages + 1):
            try:
                payload = await self._rpc(
                    "getAssetsByOwner",
                    {
                        "ownerAddress": address,
                        "page": page,
                        "limit": self.das_page_limit,
                        "displayOptions": {"showFungible": True},
                    },
                )
                items = payload["result"]["items"]
                total = int(payload["result"].get("total", len(items)))
                if not isinstance(items, list):
                    raise ValueError("invalid DAS response")
            except (httpx.HTTPError, KeyError, TypeError, ValueError):
                if page == 1:
                    raise
                return tuple(assets.values()), True

            for item in items:
                asset = self._das_asset(item)
                if asset is None:
                    continue
                previous = assets.get(asset.mint_address)
                if previous is None:
                    assets[asset.mint_address] = asset
                else:
                    assets[asset.mint_address] = WalletAsset(
                        asset.mint_address,
                        previous.symbol if previous.symbol != previous.mint_address else asset.symbol,
                        previous.quantity + asset.quantity,
                        asset.price_usd if asset.price_usd is not None else previous.price_usd,
                        None,
                    )
            if page * self.das_page_limit >= total:
                return tuple(assets.values()), False
        return tuple(assets.values()), True

    async def _rpc(self, method: str, params: list[object] | dict[str, object]) -> dict:
        response = await self.http_client.post(
            self.url,
            json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
            timeout=httpx.Timeout(8.0),
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or payload.get("error") or "result" not in payload:
            raise ValueError("invalid RPC response")
        return payload

    @staticmethod
    def _sol_asset(payload: dict) -> WalletAsset:
        lamports = int(payload["result"]["value"])
        return WalletAsset(None, "SOL", lamports / 1_000_000_000, None, None)

    @staticmethod
    def _das_asset(item: object) -> WalletAsset | None:
        if not isinstance(item, dict) or item.get("interface") not in {"FungibleToken", "FungibleAsset"}:
            return None
        mint = item.get("id")
        token_info = item.get("token_info")
        if not isinstance(mint, str) or not isinstance(token_info, dict):
            return None
        content = item.get("content")
        metadata = content.get("metadata") if isinstance(content, dict) else None
        try:
            raw_balance = Decimal(str(token_info.get("balance") or 0))
            decimals_value = token_info.get("decimals")
            if decimals_value is None and isinstance(metadata, dict):
                decimals_value = metadata.get("decimals")
            decimals = int(decimals_value or 0)
        except (InvalidOperation, TypeError, ValueError):
            return None
        if raw_balance <= 0 or decimals < 0:
            return None
        quantity = float(raw_balance / Decimal(10) ** decimals)
        symbol = metadata.get("symbol") if isinstance(metadata, dict) else None
        price_info = token_info.get("price_info")
        price = price_info.get("price_per_token") if isinstance(price_info, dict) else None
        return WalletAsset(mint, symbol if isinstance(symbol, str) and symbol else mint, quantity,
                           float(price) if price is not None else None, None)
