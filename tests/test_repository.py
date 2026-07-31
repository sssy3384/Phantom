from datetime import UTC, datetime, timedelta
import sqlite3

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


def test_top_signals_uses_only_each_pool_latest_alerted_decision(tmp_path):
    repo = Repository(tmp_path / "signals.sqlite3")
    repo.initialize()
    repo.save_decision(sample_decision("pool-kept", 91))
    repo.save_decision(sample_decision("pool-duplicate", 99))
    repo.save_decision(Decision(
        pool_address="pool-duplicate", score=70, status="watch", reasons=("cooling",),
        advice=None, observed_at=NOW + timedelta(minutes=1),
    ))
    repo.save_decision(Decision(
        pool_address="pool-rejected", score=0, status="rejected", reasons=("risk",),
        advice=None, observed_at=NOW + timedelta(minutes=1),
    ))
    repo.save_decision(sample_decision("pool-second", 90))

    assert [decision.pool_address for decision in repo.top_signals(3)] == [
        "pool-kept", "pool-second",
    ]


def test_initialize_adds_metadata_columns_to_existing_decisions_database(tmp_path):
    database = tmp_path / "signals.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute(
            """CREATE TABLE decisions (
                pool_address TEXT NOT NULL, observed_at TEXT NOT NULL, score INTEGER NOT NULL,
                status TEXT NOT NULL, reasons_json TEXT NOT NULL, advice_json TEXT,
                PRIMARY KEY (pool_address, observed_at)
            )"""
        )

    repo = Repository(database)
    repo.initialize()
    repo.save_decision(Decision(
        pool_address="pool-1", score=90, status="alerted", reasons=("quality",), advice=None,
        observed_at=NOW, token_address="token-1", symbol="DOG",
    ))

    assert repo.decision("pool-1").token_address == "token-1"


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


def test_repository_reads_old_wallet_snapshot_as_non_partial(tmp_path):
    repo = Repository(tmp_path / "signals.sqlite3")
    repo.initialize()
    old_payload = {
        "address": "wallet-1", "assets": [], "total_usd": None,
        "sampled_at": NOW.isoformat(), "error": None,
    }
    with repo._connect() as connection:
        connection.execute(
            "INSERT INTO wallet_snapshots (sampled_at, address, payload_json, error) VALUES (?, ?, ?, ?)",
            (NOW.isoformat(), "wallet-1", __import__("json").dumps(old_payload), None),
        )

    assert repo.latest_wallet_snapshot().partial is False
