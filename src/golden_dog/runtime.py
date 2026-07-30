"""In-memory scanner runtime status without wallet-identifying data."""

from dataclasses import dataclass
from datetime import datetime
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

    @property
    def running(self) -> bool:
        return self.state is RuntimeState.RUNNING
