"""SQLite persistence for read-only signal history."""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import sqlite3

from .models import Decision, TradeAdvice


ALERT_COOLDOWN_SECONDS = 6 * 60 * 60


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
                """
            )

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
