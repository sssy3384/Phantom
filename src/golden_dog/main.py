"""ASGI entry point for the read-only signal dashboard and scanner."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime
import logging
from typing import Awaitable, Callable

import httpx
from fastapi import FastAPI

from .api import create_app
from .clients.dexscreener import DexScreenerClient
from .clients.geckoterminal import GeckoTerminalClient
from .clients.helius import HeliusClient
from .clients.prices import DexPriceClient
from .clients.wallet import WalletClient
from .config import Settings
from .notifier import BarkNotifier
from .repository import Repository
from .runtime import RuntimeState, RuntimeStatus
from .scoring import score
from .service import SignalService
from .wallet import WalletService


LOGGER = logging.getLogger(__name__)
ScanOnce = Callable[[], Awaitable[object]]


async def run_scan_loop(scan_once: ScanOnce, interval_seconds: float) -> None:
    """Run one scan per interval; task cancellation deliberately propagates."""
    while True:
        await scan_once()
        await asyncio.sleep(interval_seconds)


async def run_combined_scan(
    signal_scan: ScanOnce,
    wallet_scan: ScanOnce,
    runtime: RuntimeStatus,
    now: datetime,
) -> None:
    """Run signals first; wallet sampling never interrupts signal scanning."""
    runtime.state = RuntimeState.RUNNING
    runtime.last_started_at = now
    try:
        await signal_scan()
    except asyncio.CancelledError:
        raise
    except Exception:
        runtime.last_failure_at = now
        raise

    runtime.last_success_at = now
    try:
        wallet_result = await wallet_scan()
    except asyncio.CancelledError:
        raise
    except Exception as error:
        runtime.wallet_error = type(error).__name__
        runtime.last_failure_at = now
    else:
        runtime.wallet_error = getattr(wallet_result, "error", None)
        if runtime.wallet_error is not None:
            runtime.last_failure_at = now


def create_runtime_app(
    repository: Repository,
    scan_once: ScanOnce,
    *,
    interval_seconds: float,
    runtime: RuntimeStatus | None = None,
) -> FastAPI:
    """Create an app whose scanner is started and stopped with its ASGI lifespan."""
    app = create_app(repository)
    runtime = runtime or RuntimeStatus(interval_seconds=interval_seconds)
    app.state.runtime = runtime

    async def logged_scan() -> None:
        try:
            await scan_once()
        except asyncio.CancelledError:
            raise
        except Exception as error:
            LOGGER.error("background scan failed; continuing (%s)", type(error).__name__)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        runtime.state = RuntimeState.RUNNING
        task = asyncio.create_task(run_scan_loop(logged_scan, interval_seconds))
        app.state.scan_task = task
        try:
            yield
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            runtime.state = RuntimeState.STOPPED

    app.router.lifespan_context = lifespan
    return app


def build_service(settings: Settings) -> tuple[SignalService, httpx.AsyncClient]:
    """Wire read-only sources, scoring, and optional Bark notification delivery."""
    client = httpx.AsyncClient()
    helius = HeliusClient(client, settings.helius_api_key)

    async def scorer(candidate, *, now):
        return score(candidate, await helius.enrich(candidate.token_address), now=now)

    return (
        SignalService(
            Repository(settings.database_path),
            (DexScreenerClient(client), GeckoTerminalClient(client)),
            scorer,
            BarkNotifier(client, settings.bark_base_url, settings.bark_device_key),
        ),
        client,
    )


def create_default_app(settings: Settings | None = None) -> FastAPI:
    """Build the configured runtime without performing network I/O at import time."""
    settings = settings or Settings.from_env()
    service, client = build_service(settings)
    wallet_service = WalletService(
        service.repository,
        WalletClient(client, settings.helius_api_key),
        DexPriceClient(client),
    )
    runtime = RuntimeStatus(interval_seconds=settings.scan_interval_seconds)

    async def signal_scan() -> object:
        return await service.scan_once(datetime.now(UTC))

    async def wallet_scan() -> object:
        return await wallet_service.sample(settings.watch_wallet_address, datetime.now(UTC))

    async def scan_once() -> None:
        now = datetime.now(UTC)
        await run_combined_scan(signal_scan, wallet_scan, runtime, now)

    app = create_runtime_app(
        service.repository,
        scan_once,
        interval_seconds=settings.scan_interval_seconds,
        runtime=runtime,
    )
    original_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(lifespan_app: FastAPI):
        try:
            async with original_lifespan(lifespan_app):
                yield
        finally:
            await client.aclose()

    app.router.lifespan_context = lifespan
    return app


app = create_default_app()
