from datetime import UTC, datetime
import json
import logging

import httpx
import pytest

from golden_dog.models import Candidate, Decision, TradeAdvice
from golden_dog.notifier import BarkNotifier


NOW = datetime(2026, 7, 30, 12, tzinfo=UTC)


def candidate() -> Candidate:
    return Candidate(
        pool_address="pool-1", token_address="token-1", symbol="DOG",
        discovered_at=NOW, pool_created_at=NOW, liquidity_usd=25_000,
        volume_m5_usd=3_000, buys_m5=20, sells_m5=4,
        price_change_m5_pct=20, price_usd=0.02,
    )


def decision() -> Decision:
    return Decision(
        pool_address="pool-1", score=90, status="alerted",
        reasons=("liquidity score: 25/25",),
        advice=TradeAdvice(0.021, 5, "liquidity drops below $10,000", -15, (25, 50)),
        observed_at=NOW,
    )


@pytest.mark.asyncio
async def test_bark_notifier_posts_complete_signal_payload(httpx_mock):
    httpx_mock.add_response(url="https://api.day.app/device-1", json={"code": 200})
    async with httpx.AsyncClient() as client:
        notifier = BarkNotifier(client, "https://api.day.app", "device-1")
        assert await notifier.notify(candidate(), decision()) is True

    request = httpx_mock.get_request()
    assert json.loads(request.content) == {
        "title": "Golden Dog: DOG",
        "score": 90,
        "reasons": ["liquidity score: 25/25"],
        "risks": ["liquidity drops below $10,000"],
        "advice": {
            "entry_ceiling_usd": 0.021, "max_position_pct": 5,
            "invalidation": "liquidity drops below $10,000",
            "stop_loss_pct": -15, "take_profit_pcts": [25, 50],
        },
        "detail_url": "http://localhost:8000/pools/pool-1",
    }


@pytest.mark.asyncio
async def test_bark_notifier_skips_delivery_without_credentials(caplog):
    caplog.set_level(logging.INFO)
    async with httpx.AsyncClient() as client:
        notifier = BarkNotifier(client, "https://api.day.app", None)
        assert await notifier.notify(candidate(), decision()) is False

    assert "skipped delivery" in caplog.text


@pytest.mark.asyncio
async def test_bark_http_error_log_does_not_expose_device_key(httpx_mock, caplog):
    caplog.set_level(logging.WARNING)
    httpx_mock.add_response(url="https://api.day.app/device-secret", status_code=503)
    async with httpx.AsyncClient() as client:
        notifier = BarkNotifier(client, "https://api.day.app", "device-secret")
        assert await notifier.notify(candidate(), decision()) is False

    assert "503" in caplog.text
    assert "device-secret" not in caplog.text


@pytest.mark.asyncio
async def test_bark_notifier_posts_risk_marked_revocation_payload(httpx_mock):
    httpx_mock.add_response(url="https://api.day.app/device-1", json={"code": 200})
    revoked = Decision("pool-1", 0, "rejected", ("mint authority enabled",), None, NOW)
    async with httpx.AsyncClient() as client:
        notifier = BarkNotifier(client, "https://api.day.app", "device-1")
        assert await notifier.notify_revocation(candidate(), revoked) is True

    assert json.loads(httpx_mock.get_request().content) == {
        "title": "Golden Dog revoked: DOG",
        "score": 0,
        "reasons": ["mint authority enabled"],
        "risks": ["mint authority enabled"],
        "advice": None,
        "detail_url": "http://localhost:8000/pools/pool-1",
        "event": "revocation",
    }
