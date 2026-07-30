"""Runtime integration tests for the background signal scanner."""

import asyncio
from threading import Event

import pytest
from fastapi.testclient import TestClient

from golden_dog.main import create_runtime_app, run_scan_loop
import golden_dog.main as main
from golden_dog.config import Settings
from golden_dog.repository import Repository


@pytest.mark.asyncio
async def test_run_scan_loop_scans_repeatedly_and_propagates_cancellation():
    calls = 0

    async def scan_once():
        nonlocal calls
        calls += 1

    task = asyncio.create_task(run_scan_loop(scan_once, interval_seconds=0.001))
    while calls < 2:
        await asyncio.sleep(0)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert calls >= 2


def test_runtime_lifespan_continues_after_failure_and_stops_scanner(tmp_path):
    calls = 0

    async def scan_once():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("transient source failure")

    app = create_runtime_app(
        Repository(tmp_path / "signals.sqlite3"), scan_once, interval_seconds=0.001
    )
    with TestClient(app) as client:
        while calls < 2:
            client.get("/api/health")

    assert calls >= 2
    task = app.state.scan_task
    assert task.done()
    assert task.cancelled()


def test_default_lifespan_cancels_scanner_before_closing_http_client(monkeypatch, tmp_path):
    scan_started = Event()
    scan_cancelled = Event()
    client_closed = Event()

    class Service:
        repository = Repository(tmp_path / "signals.sqlite3")

        async def scan_once(self, now):
            scan_started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                scan_cancelled.set()
                raise

    class Client:
        async def aclose(self):
            assert scan_cancelled.is_set()
            client_closed.set()

    monkeypatch.setattr(main, "build_service", lambda settings: (Service(), Client()))
    settings = Settings(None, "https://api.day.app", None, tmp_path / "signals.sqlite3", 1)
    with TestClient(main.create_default_app(settings)):
        assert scan_started.wait(timeout=1)

    assert client_closed.is_set()
