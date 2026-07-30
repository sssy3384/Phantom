from datetime import UTC, datetime, timedelta
import importlib
import inspect

from fastapi.testclient import TestClient
import pytest

from golden_dog.api import create_app, risk_flags
from golden_dog.config import Settings
from golden_dog.models import Decision, TradeAdvice
from golden_dog.repository import Repository


NOW = datetime(2026, 7, 30, 12, tzinfo=UTC)


def test_main_default_app_uses_settings_database_path(monkeypatch, tmp_path):
    database_path = tmp_path / "configured.sqlite3"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DATABASE_PATH", str(database_path))
    import golden_dog.main as main

    main = importlib.reload(main)
    health_route = next(route for route in main.app.routes if route.path == "/api/health")
    repository = inspect.getclosurevars(health_route.endpoint).nonlocals["repository"]

    assert repository.path == Settings.from_env().database_path


@pytest.fixture
def repository(tmp_path):
    repo = Repository(tmp_path / "signals.sqlite3")
    repo.initialize()
    for pool, score, status in (
        ("pool-low", 71, "alerted"),
        ("pool-high", 96, "alerted"),
        ("pool-mid", 85, "alerted"),
        ("pool-fourth", 80, "alerted"),
        ("pool-rejected", 0, "rejected"),
    ):
        repo.save_decision(
            Decision(
                pool_address=pool,
                score=score,
                status=status,
                reasons=("liquidity score: 25/25", "mint authority disabled"),
                advice=TradeAdvice(
                    entry_ceiling_usd=0.02,
                    max_position_pct=5,
                    invalidation="liquidity drops below $10,000",
                    stop_loss_pct=-15,
                    take_profit_pcts=(25, 50),
                ) if status == "alerted" else None,
                observed_at=NOW - timedelta(minutes=score),
            )
        )
    repo.save_source_health("dexscreener", NOW - timedelta(minutes=4), "stale")
    repo.save_source_health("helius", NOW, "RuntimeError")
    return repo


@pytest.fixture
def client(repository):
    with TestClient(create_app(repository, now=lambda: NOW)) as test_client:
        yield test_client


def test_health_serializes_stale_and_failed_sources(client):
    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {
        "sources": [
            {
                "source": "dexscreener",
                "status": "stale",
                "sampled_at": "2026-07-30T11:56:00+00:00",
                "error": "stale",
            },
            {
                "source": "helius",
                "status": "failed",
                "sampled_at": "2026-07-30T12:00:00+00:00",
                "error": "RuntimeError",
            },
        ]
    }


def test_fresh_repository_serves_read_only_routes_without_network(tmp_path):
    repository = Repository(tmp_path / "fresh.sqlite3")

    with TestClient(create_app(repository)) as test_client:
        for path in ("/api/health", "/api/dashboard", "/api/history"):
            assert test_client.get(path).status_code == 200


def test_dashboard_has_exact_top_level_shape_and_three_highest_signal_cards(client):
    response = client.get("/api/dashboard")

    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == {"health", "today", "signals"}
    assert payload["today"] == {"total": 5, "alerted": 4, "watch": 0, "rejected": 1}
    assert [signal["pool_address"] for signal in payload["signals"]] == [
        "pool-high", "pool-mid", "pool-fourth"
    ]
    assert payload["signals"][0]["advice"]["max_position_pct"] == 5


def test_dashboard_today_counts_only_decisions_from_current_calendar_day(repository):
    repository.save_decision(
        Decision(
            pool_address="pool-yesterday", score=99, status="alerted", reasons=("quality",),
            advice=None, observed_at=NOW - timedelta(days=1),
        )
    )
    with TestClient(create_app(repository, now=lambda: NOW)) as test_client:
        payload = test_client.get("/api/dashboard").json()

    assert payload["today"] == {"total": 5, "alerted": 4, "watch": 0, "rejected": 1}


def test_signal_detail_contains_all_scoring_evidence_risk_flags_advice_and_dexscreener_link(client):
    response = client.get("/api/signals/pool-high")

    assert response.status_code == 200
    payload = response.json()
    assert payload["reasons"] == ["liquidity score: 25/25", "mint authority disabled"]
    assert payload["risk_flags"] == []
    assert payload["advice"]["max_position_pct"] == 5
    assert payload["dexscreener_url"] == "https://dexscreener.com/solana/pool-high"


def test_signal_detail_returns_404_for_unknown_pool(client):
    assert client.get("/api/signals/unknown").status_code == 404


@pytest.mark.parametrize(
    ("reason", "expected"),
    [
        ("mint authority enabled", "mint authority enabled"),
        ("freeze authority enabled", "freeze authority enabled"),
        ("top 10 holders exceed 55%", "top 10 holders exceed 55%"),
        ("liquidity below $10,000", "liquidity below $10,000"),
        ("critical enrichment stale", "critical enrichment stale"),
        ("critical enrichment unavailable", "critical enrichment unavailable"),
        ("pool older than 4 hours", "pool older than 4 hours"),
    ],
)
def test_risk_flags_identify_scoring_gate_reasons(reason, expected):
    assert risk_flags((reason, "quality score: 30/30")) == [expected]


def test_rejected_signal_exposes_all_rejection_reasons_as_risk_flags(repository):
    repository.save_decision(
        Decision(
            pool_address="pool-rejected-risk", score=0, status="rejected",
            reasons=("mint authority enabled", "unmodelled rejection gate"), advice=None, observed_at=NOW,
        )
    )
    with TestClient(create_app(repository, now=lambda: NOW)) as test_client:
        payload = test_client.get("/api/signals/pool-rejected-risk").json()

    assert payload["risk_flags"] == ["mint authority enabled", "unmodelled rejection gate"]


def test_history_returns_serializable_full_signal_history(client):
    response = client.get("/api/history")

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["signals"]) == 5
    assert payload["signals"][0]["observed_at"].endswith("+00:00")


def test_root_serves_read_only_dashboard_without_wallet_or_order_actions(client):
    response = client.get("/")

    assert response.status_code == 200
    assert "Golden Dog Finder" in response.text
    assert "wallet" not in response.text.lower()
    assert "order" not in response.text.lower()


def test_static_dashboard_loads_signal_detail_with_evidence_advice_risks_and_link(client):
    script = client.get("/static/app.js")

    assert script.status_code == 200
    for required in ("loadSignalDetail", "reasons", "risk_flags", "advice", "dexscreener_url", "sampled_at", "error"):
        assert required in script.text
