"""Read-only FastAPI dashboard for persisted golden-dog signals."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Callable

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .models import Decision
from .repository import Repository, SourceHealth


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


def _health_item(source: str, health: SourceHealth) -> dict[str, str | None]:
    status = "healthy" if health.error is None else "stale" if health.error == "stale" else "failed"
    return {
        "source": source,
        "status": status,
        "sampled_at": health.sampled_at.isoformat(),
        "error": health.error,
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


def create_app(repository: Repository, now: Callable[[], datetime] = datetime.now) -> FastAPI:
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
        }

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
