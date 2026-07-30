"""Read-only FastAPI dashboard for persisted golden-dog signals."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from pathlib import Path
import sqlite3
from typing import Callable

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .models import Decision, WalletSnapshot
from .repository import Repository, SourceHealth
from .runtime import RuntimeStatus


STATIC_DIR = Path(__file__).parent / "static"
RISK_GATE_REASONS = frozenset((
    "mint authority enabled",
    "freeze authority enabled",
    "top 10 holders exceed 55%",
    "liquidity below $10,000",
    "critical enrichment stale",
    "critical enrichment unavailable",
    "pool older than 4 hours",
))
WALLET_UNAVAILABLE = "wallet data unavailable"
WALLET_SCAN_FAILED = "wallet scan failed"
SOURCE_UNAVAILABLE = "source unavailable"
STALE_ERROR_CODES = frozenset(("stale",))
DATABASE_READ_ERRORS = (OSError, sqlite3.Error)
DATABASE_READ_FAILED = object()


def _safe_error(
    error: str | None,
    fallback: str,
    *,
    allowed: frozenset[str] = frozenset(),
) -> str | None:
    """Return only reviewed status codes; persisted errors can contain secrets."""
    if error is None:
        return None
    return error if error in allowed else fallback


def _repository_read(read: Callable[[], object]) -> object:
    """Convert database read failures into a sentinel with no exception details."""
    try:
        return read()
    except DATABASE_READ_ERRORS:
        return DATABASE_READ_FAILED


def _degraded_dashboard(runtime: RuntimeStatus | None, repository: Repository) -> dict[str, object]:
    return {
        "health": {"sources": []},
        "today": {"total": 0, "alerted": 0, "watch": 0, "rejected": 0},
        "signals": [],
        "wallet": _wallet_payload(None),
        "runtime": _runtime_payload(runtime, repository, database_healthy=False),
    }


def _health_item(source: str, health: SourceHealth) -> dict[str, str | None]:
    status = "healthy" if health.error is None else "stale" if health.error == "stale" else "failed"
    return {
        "source": source,
        "status": status,
        "sampled_at": health.sampled_at.isoformat(),
        "error": _safe_error(health.error, SOURCE_UNAVAILABLE, allowed=STALE_ERROR_CODES),
    }


def risk_flags(reasons: tuple[str, ...], *, rejected: bool = False) -> list[str]:
    """Expose scoring gates as explicit dashboard risk flags."""
    if rejected:
        return list(reasons)
    return [reason for reason in reasons if reason.lower() in RISK_GATE_REASONS]


def _decision_payload(decision: Decision, *, detail: bool = False) -> dict[str, object]:
    payload: dict[str, object] = {
        "pool_address": decision.pool_address,
        "score": decision.score,
        "status": decision.status,
        "observed_at": decision.observed_at.isoformat(),
        "advice": asdict(decision.advice) if decision.advice else None,
    }
    if detail:
        payload.update(
            reasons=list(decision.reasons),
            risk_flags=risk_flags(decision.reasons, rejected=decision.status == "rejected"),
            dexscreener_url=f"https://dexscreener.com/solana/{decision.pool_address}",
        )
    return payload


def _wallet_payload(snapshot: WalletSnapshot | None) -> dict[str, object]:
    """Serialize persisted wallet data without exposing its watched address."""
    if snapshot is None:
        return {
            "assets": [],
            "total_usd": None,
            "sampled_at": None,
            "error": "wallet snapshot unavailable",
        }
    return {
        "assets": [asdict(asset) for asset in snapshot.assets],
        "total_usd": snapshot.total_usd,
        "sampled_at": snapshot.sampled_at.isoformat(),
        "error": _safe_error(snapshot.error, WALLET_UNAVAILABLE),
    }


def _runtime_payload(
    runtime: RuntimeStatus | None,
    repository: Repository,
    *,
    database_healthy: bool | None = None,
) -> dict[str, object]:
    """Expose scanner health only; runtime intentionally contains no credentials or address."""
    if runtime is None:
        return {
            "state": "stopped", "running": False, "interval_seconds": None,
            "last_started_at": None, "last_success_at": None, "last_failure_at": None,
            "wallet_error": None, "next_scan_at": None,
            "bark": {"configured": False, "configuration": "未配置", "delivery_status": None, "last_delivery_at": None},
            "database": {"status": "healthy" if (repository.is_healthy() if database_healthy is None else database_healthy) else "unavailable"},
        }
    return {
        "state": runtime.state.value,
        "running": runtime.running,
        "interval_seconds": runtime.interval_seconds,
        "last_started_at": runtime.last_started_at.isoformat() if runtime.last_started_at else None,
        "last_success_at": runtime.last_success_at.isoformat() if runtime.last_success_at else None,
        "last_failure_at": runtime.last_failure_at.isoformat() if runtime.last_failure_at else None,
        "wallet_error": _safe_error(runtime.wallet_error, WALLET_SCAN_FAILED),
        "next_scan_at": runtime.next_scan_at.isoformat() if runtime.next_scan_at else None,
        "bark": {
            "configured": runtime.bark_configured,
            "configuration": "已配置" if runtime.bark_configured else "未配置",
            "delivery_status": runtime.last_bark_delivery_status,
            "last_delivery_at": runtime.last_bark_delivery_at.isoformat() if runtime.last_bark_delivery_at else None,
        },
        "database": {"status": "healthy" if (repository.is_healthy() if database_healthy is None else database_healthy) else "unavailable"},
    }


def create_app(
    repository: Repository,
    now: Callable[[], datetime] = datetime.now,
    runtime: RuntimeStatus | None = None,
) -> FastAPI:
    """Create an app backed by a schema-ready repository; no scanner lifecycle is started."""
    repository.initialize()
    app = FastAPI(title="Golden Dog Finder", version="0.1.0")
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/", include_in_schema=False)
    def dashboard_page() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/api/health")
    def health() -> dict[str, object]:
        sources = _repository_read(repository.source_health)
        if sources is DATABASE_READ_FAILED:
            return {"sources": [], "database": {"status": "unavailable"}}
        assert isinstance(sources, dict)
        return {"sources": [_health_item(name, value) for name, value in sorted(sources.items())]}

    @app.get("/api/dashboard")
    def dashboard() -> dict[str, object]:
        decisions = _repository_read(repository.decisions)
        sources = _repository_read(repository.source_health)
        wallet = _repository_read(repository.latest_wallet_snapshot)
        signals = _repository_read(lambda: repository.top_signals(3))
        if DATABASE_READ_FAILED in (decisions, sources, wallet, signals):
            return _degraded_dashboard(runtime, repository)
        assert isinstance(decisions, list)
        assert isinstance(sources, dict)
        assert isinstance(signals, list)
        today_decisions = [item for item in decisions if item.observed_at.date() == now().date()]
        counts = {
            status: sum(item.status == status for item in today_decisions)
            for status in ("alerted", "watch", "rejected")
        }
        return {
            "health": {"sources": [_health_item(name, value) for name, value in sorted(sources.items())]},
            "today": {"total": len(today_decisions), **counts},
            "signals": [_decision_payload(item) for item in signals],
            "wallet": _wallet_payload(wallet),
            "runtime": _runtime_payload(runtime, repository),
        }

    @app.get("/api/wallet")
    def wallet() -> dict[str, object]:
        snapshot = _repository_read(repository.latest_wallet_snapshot)
        return _wallet_payload(None if snapshot is DATABASE_READ_FAILED else snapshot)

    @app.get("/api/signals/{pool_address}")
    def signal_detail(pool_address: str) -> dict[str, object]:
        decision = _repository_read(lambda: repository.decision(pool_address))
        if decision is DATABASE_READ_FAILED:
            raise HTTPException(status_code=503, detail="service unavailable")
        if decision is None:
            raise HTTPException(status_code=404, detail="signal not found")
        assert isinstance(decision, Decision)
        return _decision_payload(decision, detail=True)

    @app.get("/api/history")
    def history() -> dict[str, object]:
        decisions = _repository_read(repository.decisions)
        if decisions is DATABASE_READ_FAILED:
            return {"signals": [], "database": {"status": "unavailable"}}
        assert isinstance(decisions, list)
        return {"signals": [_decision_payload(item, detail=True) for item in decisions]}

    return app
