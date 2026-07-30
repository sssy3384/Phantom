"""Read-only FastAPI dashboard for persisted golden-dog signals."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from pathlib import Path
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


def _runtime_payload(runtime: RuntimeStatus | None) -> dict[str, object]:
    """Expose scanner health only; runtime intentionally contains no credentials or address."""
    if runtime is None:
        return {
            "state": "stopped", "running": False, "interval_seconds": None,
            "last_started_at": None, "last_success_at": None, "last_failure_at": None,
            "wallet_error": None,
        }
    return {
        "state": runtime.state.value,
        "running": runtime.running,
        "interval_seconds": runtime.interval_seconds,
        "last_started_at": runtime.last_started_at.isoformat() if runtime.last_started_at else None,
        "last_success_at": runtime.last_success_at.isoformat() if runtime.last_success_at else None,
        "last_failure_at": runtime.last_failure_at.isoformat() if runtime.last_failure_at else None,
        "wallet_error": _safe_error(runtime.wallet_error, WALLET_SCAN_FAILED),
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
    def health() -> dict[str, list[dict[str, str | None]]]:
        sources = repository.source_health()
        return {"sources": [_health_item(name, value) for name, value in sorted(sources.items())]}

    @app.get("/api/dashboard")
    def dashboard() -> dict[str, object]:
        decisions = repository.decisions()
        today_decisions = [item for item in decisions if item.observed_at.date() == now().date()]
        counts = {
            status: sum(item.status == status for item in today_decisions)
            for status in ("alerted", "watch", "rejected")
        }
        sources = repository.source_health()
        return {
            "health": {"sources": [_health_item(name, value) for name, value in sorted(sources.items())]},
            "today": {"total": len(today_decisions), **counts},
            "signals": [_decision_payload(item) for item in repository.top_signals(3)],
            "wallet": _wallet_payload(repository.latest_wallet_snapshot()),
            "runtime": _runtime_payload(runtime),
        }

    @app.get("/api/wallet")
    def wallet() -> dict[str, object]:
        return _wallet_payload(repository.latest_wallet_snapshot())

    @app.get("/api/signals/{pool_address}")
    def signal_detail(pool_address: str) -> dict[str, object]:
        decision = repository.decision(pool_address)
        if decision is None:
            raise HTTPException(status_code=404, detail="signal not found")
        return _decision_payload(decision, detail=True)

    @app.get("/api/history")
    def history() -> dict[str, list[dict[str, object]]]:
        return {"signals": [_decision_payload(item, detail=True) for item in repository.decisions()]}

    return app
