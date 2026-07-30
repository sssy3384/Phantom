"""Shared result contract for external market data sources."""

from dataclasses import dataclass
from datetime import UTC, datetime

import httpx

from ..models import Candidate


@dataclass(frozen=True)
class SourceResult:
    items: tuple[Candidate, ...]
    sampled_at: datetime
    source: str
    error: str | None = None


class SourceClient:
    source: str

    def __init__(self, http_client: httpx.AsyncClient) -> None:
        self.http_client = http_client

    async def get_json(self, url: str):
        response = await self.http_client.get(url, timeout=httpx.Timeout(8.0))
        response.raise_for_status()
        return response.json()

    def error_result(self, error: str) -> SourceResult:
        return SourceResult((), datetime.now(UTC), self.source, error)
