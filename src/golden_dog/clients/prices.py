"""Read-only DexScreener token price client."""

import httpx


class DexPriceClient:
    token_price_url = "https://api.dexscreener.com/tokens/v1/solana/{mint}"

    def __init__(self, http_client: httpx.AsyncClient) -> None:
        self.http_client = http_client

    async def prices(self, mints: tuple[str, ...]) -> dict[str, float | None]:
        result: dict[str, float | None] = {mint: None for mint in mints}
        for mint in result:
            try:
                response = await self.http_client.get(
                    self.token_price_url.format(mint=mint), timeout=httpx.Timeout(8.0)
                )
                response.raise_for_status()
                for pair in response.json():
                    if pair.get("baseToken", {}).get("address") == mint:
                        result[mint] = float(pair["priceUsd"])
                        break
            except (httpx.HTTPError, AttributeError, KeyError, TypeError, ValueError):
                continue
        return result
