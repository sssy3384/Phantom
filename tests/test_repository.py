from datetime import UTC, datetime

try:
    from golden_dog.models import Decision, TradeAdvice, WalletAsset, WalletSnapshot
    from golden_dog.repository import Repository
except ModuleNotFoundError:
    Decision = TradeAdvice = Repository = None


NOW = datetime(2026, 7, 30, tzinfo=UTC)


def sample_decision(pool_address: str, score: int) -> object:
    return Decision(
        pool_address=pool_address,
        score=score,
        status="alerted",
        reasons=("liquidity score: 25/25",),
        advice=TradeAdvice(
            entry_ceiling_usd=0.02,
            max_position_pct=5,
            invalidation="liquidity drops below $10,000",
            stop_loss_pct=-15,
            take_profit_pcts=(25, 50),
        ),
        observed_at=NOW,
    )


def test_repository_keeps_snapshot_and_suppresses_duplicate_alert(tmp_path):
    assert Repository is not None

    repo = Repository(tmp_path / "signals.sqlite3")
    repo.initialize()
    repo.save_decision(sample_decision("pool-1", score=90))

    assert repo.top_signals(limit=3)[0].pool_address == "pool-1"
    assert repo.claim_alert("pool-1", now=1_000) is True
    assert repo.claim_alert("pool-1", now=1_001) is False


def test_repository_round_trips_latest_wallet_snapshot(tmp_path):
    repo = Repository(tmp_path / "signals.sqlite3")
    repo.initialize()
    earlier = WalletSnapshot(
        "wallet-1", (WalletAsset("mint-a", "AAA", 1.0, 2.0, 2.0),), 2.0, NOW, None
    )
    latest = WalletSnapshot(
        "wallet-2", (WalletAsset(None, "SOL", 3.0, None, None),), None,
        datetime(2026, 7, 30, 1, tzinfo=UTC), "wallet data unavailable"
    )

    repo.save_wallet_snapshot(latest)
    repo.save_wallet_snapshot(earlier)

    assert repo.latest_wallet_snapshot() == latest
