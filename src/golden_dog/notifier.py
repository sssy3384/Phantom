"""Bark notifications for qualified read-only signals."""

from __future__ import annotations

import logging
from typing import Callable

import httpx

from .models import Candidate, Decision


LOGGER = logging.getLogger(__name__)


class BarkNotifier:
    def __init__(
        self,
        http_client: httpx.AsyncClient,
        base_url: str,
        device_key: str | None,
        detail_base_url: str = "http://localhost:8000/pools",
        on_delivery_result: Callable[[bool], None] | None = None,
    ) -> None:
        self.http_client = http_client
        self.base_url = base_url.rstrip("/")
        self.device_key = device_key
        self.detail_base_url = detail_base_url.rstrip("/")
        self.on_delivery_result = on_delivery_result

    def _record_delivery(self, delivered: bool) -> bool:
        if self.on_delivery_result is not None:
            self.on_delivery_result(delivered)
        return delivered

    async def notify(self, candidate: Candidate, decision: Decision) -> bool:
        if not self.device_key:
            LOGGER.info("skipped delivery: Bark credentials unavailable")
            return self._record_delivery(False)
        try:
            response = await self.http_client.post(
                f"{self.base_url}/{self.device_key}", json=self._payload(candidate, decision)
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as error:
            LOGGER.warning("skipped delivery: Bark HTTP status %s", error.response.status_code)
            return self._record_delivery(False)
        except httpx.HTTPError as error:
            LOGGER.warning("skipped delivery: Bark request failed (%s)", type(error).__name__)
            return self._record_delivery(False)
        return self._record_delivery(True)

    async def notify_revocation(self, candidate: Candidate, decision: Decision) -> bool:
        if not self.device_key:
            LOGGER.info("skipped delivery: Bark credentials unavailable")
            return self._record_delivery(False)
        try:
            response = await self.http_client.post(
                f"{self.base_url}/{self.device_key}", json=self._revocation_payload(candidate, decision)
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as error:
            LOGGER.warning("skipped revocation delivery: Bark HTTP status %s", error.response.status_code)
            return self._record_delivery(False)
        except httpx.HTTPError as error:
            LOGGER.warning("skipped revocation delivery: Bark request failed (%s)", type(error).__name__)
            return self._record_delivery(False)
        return self._record_delivery(True)

    def _payload(self, candidate: Candidate, decision: Decision) -> dict[str, object]:
        advice = decision.advice
        advice_payload = None
        risks: list[str] = ["No trade executed; verify independently"]
        if advice is not None:
            advice_payload = {
                "entry_ceiling_usd": advice.entry_ceiling_usd,
                "max_position_pct": advice.max_position_pct,
                "invalidation": advice.invalidation,
                "stop_loss_pct": advice.stop_loss_pct,
                "take_profit_pcts": list(advice.take_profit_pcts),
            }
            risks = [advice.invalidation]
        return {
            "title": f"Golden Dog: {candidate.symbol}",
            "score": decision.score,
            "reasons": list(decision.reasons),
            "risks": risks,
            "advice": advice_payload,
            "detail_url": f"{self.detail_base_url}/{candidate.pool_address}",
        }

    def _revocation_payload(self, candidate: Candidate, decision: Decision) -> dict[str, object]:
        return {
            "title": f"Golden Dog revoked: {candidate.symbol}",
            "score": decision.score,
            "reasons": list(decision.reasons),
            "risks": list(decision.reasons),
            "advice": None,
            "detail_url": f"{self.detail_base_url}/{candidate.pool_address}",
            "event": "revocation",
        }
