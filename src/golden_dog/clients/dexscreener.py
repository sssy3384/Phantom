"""DexScreener discovery client."""

from datetime import UTC, datetime

import httpx

from ..models import Candidate
from .base import SourceClient, SourceResult


class DexScreenerClient(SourceClient):
    source = "dexscreener"
    profiles_url = "https://api.dexscreener.com/token-profiles/latest/v1"
    token_pairs_url = "https://api.dexscreener.com/token-pairs/v1/solana/{token_address}"

    async def discover(self) -> SourceResult:
        sampled_at = datetime.now(UTC)
        try:
            profiles = await self.get_json(self.profiles_url)
            tokens = [
                profile["tokenAddress"]
                for profile in profiles
                if profile.get("chainId") == "solana" and profile.get("tokenAddress")
            ]
            candidates: list[Candidate] = []
            for token_address in dict.fromkeys(tokens):
                pairs = await self.get_json(self.token_pairs_url.format(token_address=token_address))
                candidates.extend(self._normalize_pair(pair, sampled_at) for pair in pairs)
            unique = {candidate.pool_address: candidate for candidate in candidates}
            return SourceResult(tuple(unique.values()), sampled_at, self.source)
        except httpx.TimeoutException:
            return self.error_result("timeout")
        except (httpx.HTTPError, KeyError, TypeError, ValueError):
            return self.error_result("invalid_response")

    @staticmethod
    def _normalize_pair(pair: dict, discovered_at: datetime) -> Candidate:
        created_at = datetime.fromtimestamp(pair["pairCreatedAt"] / 1000, UTC)
        txns = pair.get("txns", {}).get("m5", {})
        return Candidate(
            pool_address=pair["pairAddress"],
            token_address=pair["baseToken"]["address"],
            symbol=pair["baseToken"].get("symbol", "UNKNOWN"),
            discovered_at=discovered_at,
            pool_created_at=created_at,
            liquidity_usd=float(pair.get("liquidity", {}).get("usd") or 0),
            volume_m5_usd=float(pair.get("volume", {}).get("m5") or 0),
            buys_m5=int(txns.get("buys") or 0),
            sells_m5=int(txns.get("sells") or 0),
            price_change_m5_pct=float(pair.get("priceChange", {}).get("m5") or 0),
        )
