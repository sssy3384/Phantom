"""SQLite persistence for read-only signal history."""

from __future__ import annotations

from datetime import datetime
from dataclasses import dataclass
import json
from pathlib import Path
import secrets
import sqlite3

from .models import Decision, TradeAdvice


ALERT_COOLDOWN_SECONDS = 6 * 60 * 60
ALERT_RESERVATION_SECONDS = 5 * 60


@dataclass(frozen=True)
class SourceHealth:
    sampled_at: datetime
    error: str | None


class Repository:
    def __init__(self, path: Path) -> None:
        self.path = path

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS snapshots (
                    pool_address TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    PRIMARY KEY (pool_address, observed_at)
                );
                CREATE TABLE IF NOT EXISTS decisions (
                    pool_address TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    score INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    reasons_json TEXT NOT NULL,
                    advice_json TEXT,
                    PRIMARY KEY (pool_address, observed_at)
                );
                CREATE TABLE IF NOT EXISTS alerts (
                    pool_address TEXT PRIMARY KEY,
                    last_sent_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS source_health (
                    source TEXT PRIMARY KEY,
                    sampled_at TEXT NOT NULL,
                    error TEXT
                );
                CREATE TABLE IF NOT EXISTS daily_alerts (
                    day TEXT NOT NULL,
                    pool_address TEXT NOT NULL,
                    PRIMARY KEY (day, pool_address)
                );
                CREATE TABLE IF NOT EXISTS alert_reservations (
                    pool_address TEXT PRIMARY KEY,
                    day TEXT NOT NULL,
                    expires_at INTEGER NOT NULL,
                    owner_token TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS alert_revocations (
                    pool_address TEXT PRIMARY KEY,
                    alert_sent_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS revocation_reservations (
                    pool_address TEXT PRIMARY KEY,
                    alert_sent_at INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL,
                    owner_token TEXT NOT NULL
                );
                """
            )
            columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(alert_reservations)")
            }
            if "owner_token" not in columns:
                connection.execute("ALTER TABLE alert_reservations ADD COLUMN owner_token TEXT")

    def save_decision(self, decision: Decision) -> None:
        advice_json = None
        if decision.advice is not None:
            advice_json = json.dumps(
                {
                    "entry_ceiling_usd": decision.advice.entry_ceiling_usd,
                    "max_position_pct": decision.advice.max_position_pct,
                    "invalidation": decision.advice.invalidation,
                    "stop_loss_pct": decision.advice.stop_loss_pct,
                    "take_profit_pcts": decision.advice.take_profit_pcts,
                }
            )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO decisions
                (pool_address, observed_at, score, status, reasons_json, advice_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    decision.pool_address,
                    decision.observed_at.isoformat(),
                    decision.score,
                    decision.status,
                    json.dumps(decision.reasons),
                    advice_json,
                ),
            )

    def top_signals(self, limit: int) -> list[Decision]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM decisions
                WHERE status = 'alerted'
                ORDER BY score DESC, observed_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [self._decision_from_row(row) for row in rows]

    def claim_alert(self, pool_address: str, now: int) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT last_sent_at FROM alerts WHERE pool_address = ?", (pool_address,)
            ).fetchone()
            if row is not None and now - row["last_sent_at"] < ALERT_COOLDOWN_SECONDS:
                return False
            connection.execute(
                """
                INSERT INTO alerts (pool_address, last_sent_at) VALUES (?, ?)
                ON CONFLICT(pool_address) DO UPDATE SET last_sent_at = excluded.last_sent_at
                """,
                (pool_address, now),
            )
            return True

    def save_source_health(self, source: str, sampled_at: datetime, error: str | None) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO source_health (source, sampled_at, error) VALUES (?, ?, ?)
                ON CONFLICT(source) DO UPDATE SET
                    sampled_at = excluded.sampled_at, error = excluded.error
                """,
                (source, sampled_at.isoformat(), error),
            )

    def source_health(self) -> dict[str, SourceHealth]:
        with self._connect() as connection:
            rows = connection.execute("SELECT source, sampled_at, error FROM source_health").fetchall()
        return {
            row["source"]: SourceHealth(datetime.fromisoformat(row["sampled_at"]), row["error"])
            for row in rows
        }

    def claim_daily_alert(self, pool_address: str, day: str, limit: int = 3) -> bool:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            exists = connection.execute(
                "SELECT 1 FROM daily_alerts WHERE day = ? AND pool_address = ?", (day, pool_address)
            ).fetchone()
            if exists is not None:
                return False
            count = connection.execute(
                "SELECT COUNT(*) FROM daily_alerts WHERE day = ?", (day,)
            ).fetchone()[0]
            if count >= limit:
                return False
            connection.execute(
                "INSERT INTO daily_alerts (day, pool_address) VALUES (?, ?)", (day, pool_address)
            )
            return True

    def can_deliver_alert(self, pool_address: str, now: int, day: str, limit: int = 3) -> bool:
        with self._connect() as connection:
            alert = connection.execute(
                "SELECT last_sent_at FROM alerts WHERE pool_address = ?", (pool_address,)
            ).fetchone()
            if alert is not None and now - alert["last_sent_at"] < ALERT_COOLDOWN_SECONDS:
                return False
            exists = connection.execute(
                "SELECT 1 FROM daily_alerts WHERE day = ? AND pool_address = ?", (day, pool_address)
            ).fetchone()
            if exists is not None:
                return False
            count = connection.execute(
                "SELECT COUNT(*) FROM daily_alerts WHERE day = ?", (day,)
            ).fetchone()[0]
            return count < limit

    def reserve_alert_delivery(
        self, pool_address: str, now: int, day: str, limit: int = 3
    ) -> str | None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("DELETE FROM alert_reservations WHERE expires_at <= ?", (now,))
            reserved = connection.execute(
                "SELECT 1 FROM alert_reservations WHERE pool_address = ?", (pool_address,)
            ).fetchone()
            if reserved is not None or not self._can_deliver(connection, pool_address, now, day, limit):
                return None
            reserved_count = connection.execute(
                "SELECT COUNT(*) FROM alert_reservations WHERE day = ?", (day,)
            ).fetchone()[0]
            sent_count = connection.execute(
                "SELECT COUNT(*) FROM daily_alerts WHERE day = ?", (day,)
            ).fetchone()[0]
            if sent_count + reserved_count >= limit:
                return None
            owner_token = secrets.token_urlsafe(16)
            connection.execute(
                """
                INSERT INTO alert_reservations (pool_address, day, expires_at, owner_token)
                VALUES (?, ?, ?, ?)
                """,
                (pool_address, day, now + ALERT_RESERVATION_SECONDS, owner_token),
            )
            return owner_token

    def release_alert_reservation(self, pool_address: str, owner_token: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM alert_reservations WHERE pool_address = ? AND owner_token = ?",
                (pool_address, owner_token),
            )
            return cursor.rowcount == 1

    def record_alert_delivery(
        self, pool_address: str, owner_token: str, now: int, day: str, limit: int = 3
    ) -> bool:
        """Atomically record a delivery confirmed by the notifier."""
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            reservation = connection.execute(
                """
                SELECT 1 FROM alert_reservations
                WHERE pool_address = ? AND owner_token = ? AND day = ? AND expires_at > ?
                """,
                (pool_address, owner_token, day, now),
            ).fetchone()
            if reservation is None:
                return False
            connection.execute(
                """
                INSERT INTO alerts (pool_address, last_sent_at) VALUES (?, ?)
                ON CONFLICT(pool_address) DO UPDATE SET last_sent_at = excluded.last_sent_at
                """,
                (pool_address, now),
            )
            connection.execute(
                "INSERT INTO daily_alerts (day, pool_address) VALUES (?, ?)", (day, pool_address)
            )
            connection.execute(
                "DELETE FROM alert_reservations WHERE pool_address = ? AND owner_token = ?",
                (pool_address, owner_token),
            )
            return True

    def reserve_revocation(self, pool_address: str, now: int) -> str | None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("DELETE FROM revocation_reservations WHERE expires_at <= ?", (now,))
            alert = connection.execute(
                "SELECT last_sent_at FROM alerts WHERE pool_address = ?", (pool_address,)
            ).fetchone()
            if alert is None:
                return None
            previous = connection.execute(
                "SELECT alert_sent_at FROM alert_revocations WHERE pool_address = ?", (pool_address,)
            ).fetchone()
            if previous is not None and previous["alert_sent_at"] == alert["last_sent_at"]:
                return None
            reserved = connection.execute(
                "SELECT 1 FROM revocation_reservations WHERE pool_address = ?", (pool_address,)
            ).fetchone()
            if reserved is not None:
                return None
            owner_token = secrets.token_urlsafe(16)
            connection.execute(
                """
                INSERT INTO revocation_reservations (pool_address, alert_sent_at, expires_at, owner_token)
                VALUES (?, ?, ?, ?)
                """,
                (pool_address, alert["last_sent_at"], now + ALERT_RESERVATION_SECONDS, owner_token),
            )
            return owner_token

    def release_revocation_reservation(self, pool_address: str, owner_token: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM revocation_reservations WHERE pool_address = ? AND owner_token = ?",
                (pool_address, owner_token),
            )
            return cursor.rowcount == 1

    def record_revocation_delivery(self, pool_address: str, owner_token: str, now: int) -> bool:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            reservation = connection.execute(
                """
                SELECT alert_sent_at FROM revocation_reservations
                WHERE pool_address = ? AND owner_token = ? AND expires_at > ?
                """,
                (pool_address, owner_token, now),
            ).fetchone()
            if reservation is None:
                return False
            alert = connection.execute(
                "SELECT last_sent_at FROM alerts WHERE pool_address = ?", (pool_address,)
            ).fetchone()
            if alert is None or alert["last_sent_at"] != reservation["alert_sent_at"]:
                return False
            connection.execute(
                """
                INSERT INTO alert_revocations (pool_address, alert_sent_at) VALUES (?, ?)
                ON CONFLICT(pool_address) DO UPDATE SET alert_sent_at = excluded.alert_sent_at
                """,
                (pool_address, alert["last_sent_at"]),
            )
            connection.execute(
                "DELETE FROM revocation_reservations WHERE pool_address = ? AND owner_token = ?",
                (pool_address, owner_token),
            )
            return True

    @staticmethod
    def _can_deliver(
        connection: sqlite3.Connection, pool_address: str, now: int, day: str, limit: int
    ) -> bool:
        alert = connection.execute(
            "SELECT last_sent_at FROM alerts WHERE pool_address = ?", (pool_address,)
        ).fetchone()
        if alert is not None and now - alert["last_sent_at"] < ALERT_COOLDOWN_SECONDS:
            return False
        exists = connection.execute(
            "SELECT 1 FROM daily_alerts WHERE day = ? AND pool_address = ?", (day, pool_address)
        ).fetchone()
        if exists is not None:
            return False
        count = connection.execute("SELECT COUNT(*) FROM daily_alerts WHERE day = ?", (day,)).fetchone()[0]
        return count < limit

    @staticmethod
    def _decision_from_row(row: sqlite3.Row) -> Decision:
        advice_data = json.loads(row["advice_json"]) if row["advice_json"] else None
        advice = TradeAdvice(**advice_data) if advice_data else None
        return Decision(
            pool_address=row["pool_address"],
            score=row["score"],
            status=row["status"],
            reasons=tuple(json.loads(row["reasons_json"])),
            advice=advice,
            observed_at=datetime.fromisoformat(row["observed_at"]),
        )
