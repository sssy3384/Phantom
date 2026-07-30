"""In-memory scanner runtime status without wallet-identifying data."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum


class RuntimeState(StrEnum):
    STOPPED = "stopped"
    RUNNING = "running"


@dataclass
class RuntimeStatus:
    interval_seconds: float
    state: RuntimeState = RuntimeState.STOPPED
    last_started_at: datetime | None = None
    last_success_at: datetime | None = None
    last_failure_at: datetime | None = None
    wallet_error: str | None = None
    bark_configured: bool = False
    last_bark_delivery_at: datetime | None = None
    last_bark_delivery_status: str | None = None

    @property
    def running(self) -> bool:
        return self.state is RuntimeState.RUNNING

    @property
    def next_scan_at(self) -> datetime | None:
        base_time = self.last_success_at or self.last_started_at
        return base_time + timedelta(seconds=self.interval_seconds) if base_time else None

    def record_bark_delivery(self, delivered: bool, at: datetime) -> None:
        self.last_bark_delivery_at = at
        self.last_bark_delivery_status = "delivered" if delivered else "failed"
