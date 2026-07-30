"""Runtime status for the combined signal and wallet scanner."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from golden_dog.main import create_runtime_app, run_combined_scan
from golden_dog.models import WalletSnapshot
from golden_dog.repository import Repository
from golden_dog.runtime import RuntimeState, RuntimeStatus


def test_runtime_calculates_next_scan_from_latest_success_or_start():
    now = datetime(2026, 7, 30, 8, tzinfo=UTC)
    runtime = RuntimeStatus(
        interval_seconds=45,
        last_started_at=now - timedelta(minutes=2),
        last_success_at=now - timedelta(seconds=10),
        bark_configured=True,
    )

    assert runtime.next_scan_at == now + timedelta(seconds=35)
    assert runtime.bark_configured is True


@pytest.mark.asyncio
async def test_combined_scan_uses_completion_time_for_next_deadline():
    started_at = datetime(2026, 7, 30, 8, tzinfo=UTC)
    completed_at = started_at + timedelta(seconds=12)
    status = RuntimeStatus(interval_seconds=30)

    async def scan():
        return None

    await run_combined_scan(scan, scan, status, started_at, completed_now=lambda: completed_at)

    assert status.last_started_at == started_at
    assert status.last_success_at == completed_at
    assert status.next_scan_at == completed_at + timedelta(seconds=30)


def test_runtime_app_passes_runtime_to_dashboard(tmp_path):
    runtime = RuntimeStatus(interval_seconds=45, state=RuntimeState.RUNNING)

    async def scan():
        return None

    with TestClient(create_runtime_app(
        Repository(tmp_path / "signals.sqlite3"), scan, interval_seconds=45, runtime=runtime,
    )) as client:
        payload = client.get("/api/dashboard").json()

    assert payload["runtime"]["state"] == "running"
    assert payload["runtime"]["interval_seconds"] == 45


@pytest.mark.asyncio
async def test_combined_scan_records_wallet_failure_without_stopping_signal_scan():
    events: list[str] = []
    status = RuntimeStatus(interval_seconds=30)
    now = datetime(2026, 7, 30, 8, tzinfo=UTC)

    async def signal_scan():
        events.append("signal")

    async def wallet_scan():
        events.append("wallet")
        raise RuntimeError("wallet unavailable")

    await run_combined_scan(signal_scan, wallet_scan, status, now, completed_now=lambda: now)

    assert events == ["signal", "wallet"]
    assert status.state is RuntimeState.RUNNING
    assert status.running is True
    assert status.last_started_at == now
    assert status.last_success_at == now
    assert status.last_failure_at == now
    assert status.wallet_error == "RuntimeError"


@pytest.mark.asyncio
async def test_combined_scan_redacts_wallet_exception_details_from_runtime_status():
    status = RuntimeStatus(interval_seconds=30)
    now = datetime(2026, 7, 30, 8, tzinfo=UTC)
    secret = "api-key=top-secret"
    address = "wallet-address-123"

    async def signal_scan():
        return None

    async def wallet_scan():
        raise RuntimeError(f"request failed https://rpc.example/?{secret}&address={address}")

    await run_combined_scan(signal_scan, wallet_scan, status, now, completed_now=lambda: now)

    assert status.wallet_error == "RuntimeError"
    assert secret not in repr(status)
    assert address not in repr(status)


@pytest.mark.asyncio
async def test_combined_scan_updates_success_timing_and_clears_wallet_error():
    status = RuntimeStatus(interval_seconds=15, wallet_error="previous failure")
    now = datetime(2026, 7, 30, 8, tzinfo=UTC)

    async def scan():
        return None

    await run_combined_scan(scan, scan, status, now, completed_now=lambda: now)

    assert status.state is RuntimeState.RUNNING
    assert status.running is True
    assert status.interval_seconds == 15
    assert status.last_started_at == now
    assert status.last_success_at == now
    assert status.last_failure_at is None
    assert status.wallet_error is None


@pytest.mark.asyncio
async def test_combined_scan_records_wallet_sample_error():
    status = RuntimeStatus(interval_seconds=30)
    now = datetime(2026, 7, 30, 8, tzinfo=UTC)

    async def signal_scan():
        return None

    async def wallet_scan():
        return WalletSnapshot(None, (), None, now, "wallet data unavailable")

    await run_combined_scan(signal_scan, wallet_scan, status, now, completed_now=lambda: now)

    assert status.last_success_at == now
    assert status.last_failure_at == now
    assert status.wallet_error == "wallet data unavailable"


@pytest.mark.asyncio
async def test_combined_scan_propagates_cancellation_without_recording_failure():
    status = RuntimeStatus(interval_seconds=30)
    now = datetime(2026, 7, 30, 8, tzinfo=UTC)
    wallet_called = False

    async def signal_scan():
        raise asyncio.CancelledError

    async def wallet_scan():
        nonlocal wallet_called
        wallet_called = True

    with pytest.raises(asyncio.CancelledError):
        await run_combined_scan(signal_scan, wallet_scan, status, now, completed_now=lambda: now)

    assert wallet_called is False
    assert status.last_started_at == now
    assert status.last_success_at is None
    assert status.last_failure_at is None
    assert status.wallet_error is None
