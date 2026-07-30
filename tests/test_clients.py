import json
from pathlib import Path

import httpx
import pytest

try:
    from golden_dog.clients.dexscreener import DexScreenerClient
    from golden_dog.clients.geckoterminal import GeckoTerminalClient
except ModuleNotFoundError:
    DexScreenerClient = GeckoTerminalClient = None


FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str):
    return json.loads((FIXTURES / name).read_text())


@pytest.mark.asyncio
async def test_dexscreener_returns_only_solana_candidates(httpx_mock):
    assert DexScreenerClient is not None
    httpx_mock.add_response(
        url="https://api.dexscreener.com/token-profiles/latest/v1",
        json=load_fixture("dexscreener_profiles.json"),
    )
    httpx_mock.add_response(
        url="https://api.dexscreener.com/token-pairs/v1/solana/token-1",
        json=load_fixture("dexscreener_pairs.json"),
    )

    async with httpx.AsyncClient() as http_client:
        result = await DexScreenerClient(http_client).discover()

    assert result.error is None
    assert {item.pool_address for item in result.items} == {"sol-pool-1"}


@pytest.mark.asyncio
async def test_geckoterminal_timeout_becomes_source_error(httpx_mock):
    assert GeckoTerminalClient is not None
    httpx_mock.add_exception(httpx.TimeoutException("slow"))

    async with httpx.AsyncClient() as http_client:
        result = await GeckoTerminalClient(http_client).discover()

    assert result.error == "timeout"


@pytest.mark.asyncio
async def test_geckoterminal_normalizes_new_solana_pool(httpx_mock):
    assert GeckoTerminalClient is not None
    httpx_mock.add_response(
        url="https://api.geckoterminal.com/api/v2/networks/solana/new_pools",
        json=load_fixture("geckoterminal_pools.json"),
    )

    async with httpx.AsyncClient() as http_client:
        result = await GeckoTerminalClient(http_client).discover()

    assert result.error is None
    assert result.items[0].pool_address == "sol-pool-2"
    assert result.items[0].liquidity_usd == 22000
