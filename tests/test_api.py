from datetime import UTC, datetime, timedelta
import importlib
import inspect
import sqlite3

from fastapi.testclient import TestClient
import pytest

from golden_dog.api import create_app, risk_flags
from golden_dog.config import Settings
from golden_dog.models import Decision, TradeAdvice, WalletAsset, WalletSnapshot
from golden_dog.repository import Repository
from golden_dog.runtime import RuntimeState, RuntimeStatus


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
    runtime = RuntimeStatus(
        interval_seconds=30,
        state=RuntimeState.RUNNING,
        last_started_at=NOW - timedelta(minutes=5),
        last_success_at=NOW - timedelta(minutes=1),
        bark_configured=True,
    )
    with TestClient(create_app(repository, now=lambda: NOW, runtime=runtime)) as test_client:
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
                "error": "source unavailable",
            },
        ]
    }


def test_source_health_errors_are_fixed_safe_messages_in_health_and_dashboard(repository):
    secret = "api-key=SECRET address=private-wallet"
    repository.save_source_health("untrusted-source", NOW, secret)
    with TestClient(create_app(repository, now=lambda: NOW)) as test_client:
        health = test_client.get("/api/health")
        dashboard = test_client.get("/api/dashboard")

    health_source = next(item for item in health.json()["sources"] if item["source"] == "untrusted-source")
    dashboard_source = next(item for item in dashboard.json()["health"]["sources"] if item["source"] == "untrusted-source")
    assert health_source["status"] == "failed"
    assert health_source["error"] == "source unavailable"
    assert dashboard_source == health_source
    assert secret not in health.text
    assert secret not in dashboard.text


def test_source_health_preserves_stale_error_code(client):
    response = client.get("/api/health")

    assert response.json()["sources"][0]["error"] == "stale"


def test_fresh_repository_serves_read_only_routes_without_network(tmp_path):
    repository = Repository(tmp_path / "fresh.sqlite3")

    with TestClient(create_app(repository)) as test_client:
        for path in ("/api/health", "/api/dashboard", "/api/history"):
            assert test_client.get(path).status_code == 200


def test_dashboard_has_exact_top_level_shape_and_three_highest_signal_cards(client):
    response = client.get("/api/dashboard")

    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == {"health", "today", "signals", "wallet", "runtime"}
    assert payload["today"] == {"total": 5, "alerted": 4, "watch": 0, "rejected": 1}
    assert [signal["pool_address"] for signal in payload["signals"]] == [
        "pool-high", "pool-mid", "pool-fourth"
    ]
    assert payload["signals"][0]["advice"]["max_position_pct"] == 5
    assert payload["wallet"] == {
        "assets": [], "total_usd": None, "sampled_at": None,
        "stale": False, "error": "wallet snapshot unavailable",
    }
    assert payload["runtime"] == {
        "state": "running", "running": True, "interval_seconds": 30,
        "last_started_at": "2026-07-30T11:55:00+00:00",
        "last_success_at": "2026-07-30T11:59:00+00:00",
        "last_failure_at": None, "wallet_error": None,
        "next_scan_at": "2026-07-30T11:59:30+00:00",
        "bark": {
            "configured": True, "configuration": "已配置",
            "delivery_status": None, "last_delivery_at": None,
        },
        "database": {"status": "healthy"},
    }


def test_signal_api_includes_persisted_token_metadata(repository):
    repository.save_decision(Decision(
        pool_address="pool-metadata", token_address="token-metadata", symbol="DOGE",
        score=99, status="alerted", reasons=("quality",), advice=None, observed_at=NOW,
    ))
    with TestClient(create_app(repository, now=lambda: NOW)) as test_client:
        dashboard_signal = next(
            item for item in test_client.get("/api/dashboard").json()["signals"]
            if item["pool_address"] == "pool-metadata"
        )
        detail = test_client.get("/api/signals/pool-metadata").json()

    assert dashboard_signal["token_address"] == "token-metadata"
    assert dashboard_signal["symbol"] == "DOGE"
    assert detail["dexscreener_url"] == "https://dexscreener.com/solana/pool-metadata"


def test_dashboard_reports_unavailable_database_without_raw_probe_error(repository, monkeypatch):
    monkeypatch.setattr(repository, "is_healthy", lambda: False, raising=False)
    with TestClient(create_app(repository, now=lambda: NOW)) as test_client:
        response = test_client.get("/api/dashboard")

    assert response.status_code == 200
    assert response.json()["runtime"]["database"] == {"status": "unavailable"}


def test_dashboard_degrades_safely_when_a_repository_read_raises(repository, monkeypatch):
    def broken_read():
        raise sqlite3.OperationalError("database key=SECRET")

    monkeypatch.setattr(repository, "decisions", broken_read)
    with TestClient(create_app(repository, now=lambda: NOW)) as test_client:
        response = test_client.get("/api/dashboard")

    assert response.status_code == 200
    assert response.json()["signals"] == []
    assert response.json()["wallet"] == {
        "assets": [], "total_usd": None, "sampled_at": None,
        "stale": False, "error": "wallet snapshot unavailable",
    }
    assert response.json()["runtime"]["database"] == {"status": "unavailable"}
    assert "key=SECRET" not in response.text


def test_all_read_routes_degrade_without_exposing_database_errors(repository, monkeypatch):
    app = create_app(repository, now=lambda: NOW)

    def broken_connection():
        raise sqlite3.OperationalError("database key=SECRET")

    monkeypatch.setattr(repository, "_connect", broken_connection)
    with TestClient(app) as test_client:
        wallet = test_client.get("/api/wallet")
        health = test_client.get("/api/health")
        history = test_client.get("/api/history")
        signal = test_client.get("/api/signals/pool-high")

    assert wallet.status_code == 200
    assert wallet.json() == {
        "assets": [], "total_usd": None, "sampled_at": None,
        "stale": False, "error": "wallet snapshot unavailable",
    }
    assert health.status_code == 200
    assert health.json() == {"sources": [], "database": {"status": "unavailable"}}
    assert history.status_code == 200
    assert history.json() == {"signals": [], "database": {"status": "unavailable"}}
    assert signal.status_code == 503
    assert signal.json() == {"detail": "service unavailable"}
    for response in (wallet, health, history, signal):
        assert "key=SECRET" not in response.text


def test_wallet_returns_latest_snapshot_without_wallet_address(repository):
    repository.save_wallet_snapshot(WalletSnapshot(
        "private-wallet-address", (WalletAsset("mint-1", "<SOL>", 2.5, 100, 250),),
        250, NOW, None,
    ))
    with TestClient(create_app(repository, now=lambda: NOW)) as test_client:
        response = test_client.get("/api/wallet")

    assert response.status_code == 200
    assert response.json() == {
        "assets": [{"mint_address": "mint-1", "symbol": "<SOL>", "quantity": 2.5,
                    "price_usd": 100, "usd_value": 250}],
        "total_usd": 250, "sampled_at": "2026-07-30T12:00:00+00:00",
        "stale": False, "error": None,
    }
    assert "private-wallet-address" not in response.text


def test_wallet_retains_last_successful_snapshot_when_latest_sample_failed(repository):
    successful_at = NOW - timedelta(minutes=1)
    repository.save_wallet_snapshot(WalletSnapshot(
        "private-wallet-address", (WalletAsset("mint-1", "SOL", 2.5, 100, 250),),
        250, successful_at, None,
    ))
    repository.save_wallet_snapshot(WalletSnapshot(
        "private-wallet-address", (), None, NOW, "RPC failed: api-key=SECRET",
    ))

    with TestClient(create_app(repository, now=lambda: NOW)) as test_client:
        wallet = test_client.get("/api/wallet")
        dashboard = test_client.get("/api/dashboard")

    expected = {
        "assets": [{"mint_address": "mint-1", "symbol": "SOL", "quantity": 2.5,
                    "price_usd": 100, "usd_value": 250}],
        "total_usd": 250,
        "sampled_at": successful_at.isoformat(),
        "stale": True,
        "error": "wallet data unavailable",
    }
    assert wallet.json() == expected
    assert dashboard.json()["wallet"] == expected
    assert "api-key=SECRET" not in wallet.text


def test_wallet_without_snapshot_returns_safe_unavailable_payload(repository):
    with TestClient(create_app(repository, now=lambda: NOW)) as test_client:
        response = test_client.get("/api/wallet")

    assert response.status_code == 200
    assert response.json() == {
        "assets": [], "total_usd": None, "sampled_at": None,
        "stale": False, "error": "wallet snapshot unavailable",
    }


def test_wallet_and_runtime_errors_are_fixed_safe_messages(repository):
    secret = "address=wallet-private&token=api-key-SECRET"
    repository.save_wallet_snapshot(WalletSnapshot(
        "wallet-private", (), None, NOW, f"RPC failed: {secret}",
    ))
    runtime = RuntimeStatus(interval_seconds=30, wallet_error=f"request failed: {secret}")
    with TestClient(create_app(repository, now=lambda: NOW, runtime=runtime)) as test_client:
        wallet = test_client.get("/api/wallet")
        dashboard = test_client.get("/api/dashboard")

    assert wallet.json()["error"] == "wallet data unavailable"
    assert dashboard.json()["wallet"]["error"] == "wallet data unavailable"
    assert dashboard.json()["runtime"]["wallet_error"] == "wallet scan failed"
    assert secret not in wallet.text
    assert secret not in dashboard.text
    assert "wallet-private" not in dashboard.text


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


def test_root_serves_chinese_read_only_dashboard_without_wallet_actions(client):
    response = client.get("/")

    assert response.status_code == 200
    for required in ("钱包总额", "系统状态", "信号摘要", "钱包资产", "运行状态", "数据源状态"):
        assert required in response.text
    for forbidden in ("connect wallet", "sign transaction", "place order", "buy token", "sell token", "连接钱包", "签名交易", "下单", "买入", "卖出"):
        assert forbidden not in response.text.lower()


def test_static_dashboard_loads_signal_detail_with_evidence_advice_risks_and_link(client):
    script = client.get("/static/app.js")

    assert script.status_code == 200
    for required in ("loadSignalDetail", "reasons", "risk_flags", "advice", "dexscreener_url", "sampled_at", "error"):
        assert required in script.text
    for required in ("钱包资产", "运行状态", "数据源状态", "下一次扫描", "Bark 配置", "暂无投递数据", "交易池", "代币 Mint", "symbol", "escapeHtml", "<table"):
        assert required in script.text
    for forbidden in ("connect wallet", "sign transaction", "place order", "buy token", "sell token"):
        assert forbidden not in script.text.lower()


def test_dashboard_styles_wrap_runtime_statuses_and_long_addresses(client):
    stylesheet = client.get("/static/styles.css")

    assert stylesheet.status_code == 200
    for required in ("#runtime", "flex-wrap: wrap", "overflow-wrap: anywhere", "@media (max-width: 480px)"):
        assert required in stylesheet.text
