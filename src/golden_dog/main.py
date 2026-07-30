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
from .config import Settings
from .notifier import BarkNotifier
from .repository import Repository
from .scoring import score
from .service import SignalService


LOGGER = logging.getLogger(__name__)
ScanOnce = Callable[[], Awaitable[object]]


async def run_scan_loop(scan_once: ScanOnce, interval_seconds: float) -> None:
    """Run one scan per interval; task cancellation deliberately propagates."""
    while True:
        await scan_once()
        await asyncio.sleep(interval_seconds)


def create_runtime_app(
    repository: Repository, scan_once: ScanOnce, *, interval_seconds: float
) -> FastAPI:
    """Create an app whose scanner is started and stopped with its ASGI lifespan."""
    app = create_app(repository)

    async def logged_scan() -> None:
        try:
            await scan_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            LOGGER.exception("background scan failed; continuing")

    @asynccontextmanager
    async def lifespan(_: FastAPI):
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

    async def scan_once() -> object:
        return await service.scan_once(datetime.now(UTC))

    app = create_runtime_app(
        service.repository, scan_once, interval_seconds=settings.scan_interval_seconds
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
