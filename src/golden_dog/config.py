"""Runtime configuration loaded from the local environment."""

from dataclasses import dataclass
import os
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    helius_api_key: str | None
    bark_base_url: str
    bark_device_key: str | None
    database_path: Path
    scan_interval_seconds: int = 30
    alert_score: int = 85

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            helius_api_key=os.getenv("HELIUS_API_KEY") or None,
            bark_base_url=os.getenv("BARK_BASE_URL", "https://api.day.app").rstrip("/"),
            bark_device_key=os.getenv("BARK_DEVICE_KEY") or None,
            database_path=Path(os.getenv("DATABASE_PATH", "data/golden_dog.sqlite3")),
        )
