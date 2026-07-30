from datetime import UTC, datetime, timedelta
import asyncio

import pytest

from golden_dog.clients.base import SourceResult
from golden_dog.models import Candidate, Decision
from golden_dog.repository import Repository
from golden_dog.service import SignalService


NOW = datetime(2026, 7, 30, 12, tzinfo=UTC)


def candidate(pool_address: str) -> Candidate:
    return Candidate(
        pool_address=pool_address, token_address=f"token-{pool_address}", symbol="DOG",
        discovered_at=NOW, pool_created_at=NOW, liquidity_usd=25_000,
        volume_m5_usd=3_000, buys_m5=20, sells_m5=4,
        price_change_m5_pct=20,
    )


class Source:
    def __init__(self, name, items, sampled_at=NOW, error=None, critical=False):
        self.source = name
        self.result = SourceResult(tuple(items), sampled_at, name, error)
        self.critical = critical

    async def discover(self):
        return self.result


class BrokenSource:
    source = "broken"
    critical = True

    async def discover(self):
        raise RuntimeError("upstream exploded")


class Notifier:
    def __init__(self, succeeds=True):
        self.sent = []
        self.revoked = []
        self.succeeds = succeeds
        self.revocation_succeeds = succeeds

    async def notify(self, item, decision):
        self.sent.append((item.pool_address, decision.score))
        return self.succeeds

    async def notify_revocation(self, item, decision):
        self.revoked.append((item.pool_address, decision.reasons))
        return self.revocation_succeeds


class ExplodingNotifier(Notifier):
    def __init__(self):
        super().__init__()
        self.explodes = True

    async def notify(self, item, decision):
        self.sent.append((item.pool_address, decision.score))
        if self.explodes:
            raise RuntimeError("Bark unavailable")
        return True


class YieldingNotifier(Notifier):
    async def notify(self, item, decision):
        self.sent.append((item.pool_address, decision.score))
        await asyncio.sleep(0)
        return True


class DelayedFirstNotifier(Notifier):
    def __init__(self):
        super().__init__()
        self.first_started = asyncio.Event()
        self.allow_first = asyncio.Event()
        self.calls = 0

    async def notify(self, item, decision):
        self.calls += 1
        self.sent.append((item.pool_address, decision.score))
        if self.calls == 1:
            self.first_started.set()
            await self.allow_first.wait()
        return True


class YieldingRevocationNotifier(Notifier):
    async def notify_revocation(self, item, decision):
        self.revoked.append((item.pool_address, decision.reasons))
        await asyncio.sleep(0)
        return True


def scorer(item, *, now):
    return Decision(item.pool_address, 90, "alerted", ("quality",), None, now)


def rejected_scorer(item, *, now):
    return Decision(item.pool_address, 0, "rejected", ("mint authority enabled",), None, now)


@pytest.mark.asyncio
async def test_scan_sends_one_bark_for_new_qualified_pool_and_dedupes_for_six_hours(tmp_path):
    repo = Repository(tmp_path / "signals.sqlite3")
    notifier = Notifier()
    service = SignalService(repo, [Source("dex", [candidate("pool-1")])], scorer, notifier)

    await service.scan_once(NOW)
    await service.scan_once(NOW + timedelta(hours=1))

    assert notifier.sent == [("pool-1", 90)]
    assert repo.top_signals(3)[0].pool_address == "pool-1"


@pytest.mark.asyncio
async def test_stale_critical_source_suppresses_alerts_but_persists_decisions_and_health(tmp_path):
    repo = Repository(tmp_path / "signals.sqlite3")
    notifier = Notifier()
    source = Source("dex", [candidate("pool-1")], NOW - timedelta(minutes=4), critical=True)
    service = SignalService(repo, [source], scorer, notifier)

    await service.scan_once(NOW)

    assert notifier.sent == []
    assert repo.top_signals(3)[0].pool_address == "pool-1"
    assert repo.source_health()["dex"].error == "stale"


@pytest.mark.asyncio
async def test_daily_top_three_cap_selects_highest_scoring_new_qualified_pools(tmp_path):
    repo = Repository(tmp_path / "signals.sqlite3")
    notifier = Notifier()
    scores = {"pool-0": 90, "pool-1": 95, "pool-2": 85, "pool-3": 100}

    def ranked_scorer(item, *, now):
        return Decision(item.pool_address, scores[item.pool_address], "alerted", ("quality",), None, now)

    pools = [candidate(pool) for pool in scores]
    service = SignalService(repo, [Source("dex", pools)], ranked_scorer, notifier)

    await service.scan_once(NOW)

    assert notifier.sent == [("pool-3", 100), ("pool-1", 95), ("pool-0", 90)]


@pytest.mark.asyncio
async def test_failed_delivery_does_not_consume_daily_or_six_hour_alert_claim(tmp_path):
    repo = Repository(tmp_path / "signals.sqlite3")
    notifier = Notifier(succeeds=False)
    service = SignalService(repo, [Source("dex", [candidate("pool-1")])], scorer, notifier)

    await service.scan_once(NOW)
    notifier.succeeds = True
    await service.scan_once(NOW + timedelta(minutes=1))

    assert notifier.sent == [("pool-1", 90), ("pool-1", 90)]


@pytest.mark.asyncio
async def test_cooldown_pool_does_not_displace_lower_scoring_new_qualified_pool(tmp_path):
    repo = Repository(tmp_path / "signals.sqlite3")
    repo.initialize()
    repo.claim_alert("pool-3", int(NOW.timestamp()))
    notifier = Notifier()
    scores = {"pool-0": 90, "pool-1": 95, "pool-2": 85, "pool-3": 100}

    def ranked_scorer(item, *, now):
        return Decision(item.pool_address, scores[item.pool_address], "alerted", ("quality",), None, now)

    service = SignalService(repo, [Source("dex", [candidate(pool) for pool in scores])], ranked_scorer, notifier)

    await service.scan_once(NOW + timedelta(minutes=1))

    assert notifier.sent == [("pool-1", 95), ("pool-0", 90), ("pool-2", 85)]


@pytest.mark.asyncio
async def test_concurrent_scans_reserve_alert_before_bark_delivery(tmp_path):
    repo = Repository(tmp_path / "signals.sqlite3")
    notifier = YieldingNotifier()
    service = SignalService(repo, [Source("dex", [candidate("pool-1")])], scorer, notifier)

    await asyncio.gather(service.scan_once(NOW), service.scan_once(NOW))

    assert notifier.sent == [("pool-1", 90)]


@pytest.mark.asyncio
async def test_notifier_exception_does_not_abort_scan_and_releases_reservation(tmp_path):
    repo = Repository(tmp_path / "signals.sqlite3")
    notifier = ExplodingNotifier()
    service = SignalService(repo, [Source("dex", [candidate("pool-1")])], scorer, notifier)

    await service.scan_once(NOW)
    notifier.explodes = False
    await service.scan_once(NOW + timedelta(minutes=1))

    assert notifier.sent == [("pool-1", 90), ("pool-1", 90)]


@pytest.mark.asyncio
async def test_source_exception_persists_health_and_critical_source_suppresses_alerts(tmp_path):
    repo = Repository(tmp_path / "signals.sqlite3")
    notifier = Notifier()
    service = SignalService(
        repo, [BrokenSource(), Source("dex", [candidate("pool-1")])], scorer, notifier
    )

    decisions = await service.scan_once(NOW)

    assert [decision.pool_address for decision in decisions] == ["pool-1"]
    assert repo.source_health()["broken"].error == "RuntimeError"
    assert notifier.sent == []


@pytest.mark.asyncio
async def test_expired_slow_scan_cannot_finalize_or_release_new_owner_reservation(tmp_path):
    repo = Repository(tmp_path / "signals.sqlite3")
    notifier = DelayedFirstNotifier()
    service = SignalService(repo, [Source("dex", [candidate("pool-1")])], scorer, notifier)
    later = NOW + timedelta(minutes=6)

    first_scan = asyncio.create_task(service.scan_once(NOW))
    await notifier.first_started.wait()
    await service.scan_once(later)
    notifier.allow_first.set()
    await first_scan

    assert notifier.sent == [("pool-1", 90), ("pool-1", 90)]
    assert repo.claim_alert("pool-1", int(later.timestamp()) + 1) is False
    assert repo.claim_daily_alert("pool-1", later.date().isoformat()) is False


def test_expired_reservation_owner_cannot_release_or_finalize_replacement_owner(tmp_path):
    repo = Repository(tmp_path / "signals.sqlite3")
    repo.initialize()
    first_owner = repo.reserve_alert_delivery("pool-1", int(NOW.timestamp()), NOW.date().isoformat())
    later = NOW + timedelta(minutes=6)
    second_owner = repo.reserve_alert_delivery("pool-1", int(later.timestamp()), later.date().isoformat())

    assert isinstance(first_owner, str)
    assert isinstance(second_owner, str)
    assert first_owner != second_owner
    assert repo.release_alert_reservation("pool-1", first_owner) is False
    assert repo.record_alert_delivery("pool-1", second_owner, int(later.timestamp()), later.date().isoformat())
    assert not repo.record_alert_delivery("pool-1", first_owner, int(later.timestamp()), later.date().isoformat())


@pytest.mark.asyncio
async def test_rejected_previously_alerted_pool_sends_one_revocation(tmp_path):
    repo = Repository(tmp_path / "signals.sqlite3")
    notifier = Notifier()
    service = SignalService(repo, [Source("dex", [candidate("pool-1")])], scorer, notifier)
    await service.scan_once(NOW)
    service.scorer = rejected_scorer

    await service.scan_once(NOW + timedelta(minutes=1))
    await service.scan_once(NOW + timedelta(minutes=2))

    assert notifier.revoked == [("pool-1", ("mint authority enabled",))]


@pytest.mark.asyncio
async def test_failed_revocation_is_retryable_without_duplicate_success(tmp_path):
    repo = Repository(tmp_path / "signals.sqlite3")
    notifier = Notifier()
    service = SignalService(repo, [Source("dex", [candidate("pool-1")])], scorer, notifier)
    await service.scan_once(NOW)
    service.scorer = rejected_scorer
    notifier.revocation_succeeds = False

    await service.scan_once(NOW + timedelta(minutes=1))
    notifier.revocation_succeeds = True
    await service.scan_once(NOW + timedelta(minutes=2))
    await service.scan_once(NOW + timedelta(minutes=3))

    assert notifier.revoked == [
        ("pool-1", ("mint authority enabled",)),
        ("pool-1", ("mint authority enabled",)),
    ]


@pytest.mark.asyncio
async def test_concurrent_rejection_scans_reserve_one_revocation(tmp_path):
    repo = Repository(tmp_path / "signals.sqlite3")
    notifier = YieldingRevocationNotifier()
    service = SignalService(repo, [Source("dex", [candidate("pool-1")])], scorer, notifier)
    await service.scan_once(NOW)
    service.scorer = rejected_scorer

    await asyncio.gather(
        service.scan_once(NOW + timedelta(minutes=1)), service.scan_once(NOW + timedelta(minutes=1))
    )

    assert notifier.revoked == [("pool-1", ("mint authority enabled",))]
