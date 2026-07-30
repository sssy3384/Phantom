"""Read-only public Solana wallet balance client."""

from datetime import UTC, datetime

import httpx

from ..models import WalletAsset, WalletSnapshot


class WalletClient:
    token_program_id = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"

    def __init__(self, http_client: httpx.AsyncClient, api_key: str | None) -> None:
        self.http_client = http_client
        self.api_key = api_key

    @property
    def url(self) -> str:
        return f"https://mainnet.helius-rpc.com/?api-key={self.api_key}"

    async def snapshot(self, address: str | None) -> WalletSnapshot | None:
        if not self.api_key or not address:
            return None
        try:
            balance = await self._rpc("getBalance", [address])
            token_accounts = await self._rpc(
                "getTokenAccountsByOwner",
                [address, {"programId": self.token_program_id}, {"encoding": "jsonParsed"}],
            )
            assets = (self._sol_asset(balance), *self._token_assets(token_accounts))
            return WalletSnapshot(address, assets, None, datetime.now(UTC), None)
        except (httpx.HTTPError, KeyError, TypeError, ValueError):
            return None

    async def _rpc(self, method: str, params: list[object]) -> dict:
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
    def _token_assets(payload: dict) -> tuple[WalletAsset, ...]:
        assets = []
        for account in payload["result"]["value"]:
            info = account["account"]["data"]["parsed"]["info"]
            quantity = float(info["tokenAmount"].get("uiAmount") or 0)
            if quantity > 0:
                mint = info["mint"]
                assets.append(WalletAsset(mint, mint, quantity, None, None))
        return tuple(assets)
