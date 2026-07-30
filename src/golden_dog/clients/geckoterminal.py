"""GeckoTerminal new-pool discovery client."""

from datetime import datetime

import httpx

from ..models import Candidate
from .base import SourceClient, SourceResult


class GeckoTerminalClient(SourceClient):
    source = "geckoterminal"
    pools_url = "https://api.geckoterminal.com/api/v2/networks/solana/new_pools"

    async def discover(self) -> SourceResult:
        sampled_at = datetime.now().astimezone()
        try:
            payload = await self.get_json(self.pools_url)
            candidates = tuple(self._normalize_pool(pool, sampled_at) for pool in payload["data"])
            return SourceResult(candidates, sampled_at, self.source)
        except httpx.TimeoutException:
            return self.error_result("timeout")
        except (httpx.HTTPError, KeyError, TypeError, ValueError):
            return self.error_result("invalid_response")

    @staticmethod
    def _normalize_pool(pool: dict, discovered_at: datetime) -> Candidate:
        attributes = pool["attributes"]
        txns = attributes.get("transactions", {}).get("m5", {})
        created_at = datetime.fromisoformat(attributes["pool_created_at"].replace("Z", "+00:00"))
        return Candidate(
            pool_address=attributes["address"],
            token_address=attributes["base_token_address"],
            symbol=attributes.get("name", "UNKNOWN").split(" / ")[0],
            discovered_at=discovered_at,
            pool_created_at=created_at,
            liquidity_usd=float(attributes.get("reserve_in_usd") or 0),
            volume_m5_usd=float(attributes.get("volume_usd", {}).get("m5") or 0),
            buys_m5=int(txns.get("buys") or 0),
            sells_m5=int(txns.get("sells") or 0),
            price_change_m5_pct=float(attributes.get("price_change_percentage", {}).get("m5") or 0),
        )
