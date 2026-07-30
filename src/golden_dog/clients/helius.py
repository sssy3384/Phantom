"""Read-only Helius RPC enrichment for candidate safety checks."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx

from ..scoring import Enrichment


class HeliusClient:
    def __init__(self, http_client: httpx.AsyncClient, api_key: str | None) -> None:
        self.http_client = http_client
        self.api_key = api_key

    @property
    def url(self) -> str:
        return f"https://mainnet.helius-rpc.com/?api-key={self.api_key}"

    async def enrich(self, token_address: str) -> Enrichment | None:
        if not self.api_key:
            return None
        try:
            account, largest, supply = await self._fetch_all(token_address)
            info = account["result"]["value"]["data"]["parsed"]["info"]
            total_supply = int(supply["result"]["value"]["amount"])
            largest_amount = sum(int(item["amount"]) for item in largest["result"]["value"][:10])
            if total_supply <= 0:
                return None
            return Enrichment(
                mint_authority=info.get("mintAuthority") is not None,
                freeze_authority=info.get("freezeAuthority") is not None,
                top10_holder_pct=(largest_amount / total_supply) * 100,
                sampled_at=datetime.now(UTC),
            )
        except (httpx.HTTPError, KeyError, TypeError, ValueError, IndexError):
            return None

    async def _fetch_all(self, token_address: str) -> tuple[dict, dict, dict]:
        account = await self._rpc("getAccountInfo", [token_address, {"encoding": "jsonParsed"}])
        largest = await self._rpc("getTokenLargestAccounts", [token_address])
        supply = await self._rpc("getTokenSupply", [token_address])
        return account, largest, supply

    async def _rpc(self, method: str, params: list[object]) -> dict:
        response = await self.http_client.post(
            self.url,
            json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
            timeout=httpx.Timeout(8.0),
        )
        response.raise_for_status()
        return response.json()
