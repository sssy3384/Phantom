"""Read-only public wallet balance and price clients."""

from datetime import UTC, datetime
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from golden_dog.api import create_app
from golden_dog.clients.prices import DexPriceClient
from golden_dog.clients.wallet import WalletClient
from golden_dog.config import Settings
from golden_dog.models import WalletAsset, WalletSnapshot
from golden_dog.repository import Repository
from golden_dog.wallet import WalletService


def test_settings_reads_optional_watch_wallet_address(monkeypatch):
    monkeypatch.setenv("WATCH_WALLET_ADDRESS", "wallet-1")

    assert Settings.from_env().watch_wallet_address == "wallet-1"


def test_wallet_models_are_immutable():
    asset = WalletAsset("mint-1", "TOKEN", 2.0, 1.5, 3.0)
    snapshot = WalletSnapshot("wallet-1", (asset,), 3.0, datetime.now(UTC), None)

    with pytest.raises(AttributeError):
        snapshot.address = "wallet-2"  # type: ignore[misc]

    assert snapshot.partial is False


@pytest.mark.asyncio
async def test_wallet_client_returns_none_without_helius_key(httpx_mock):
    async with httpx.AsyncClient() as http_client:
        snapshot = await WalletClient(http_client, None).snapshot("wallet-1")

    assert snapshot is None
    assert not httpx_mock.get_requests()


@pytest.mark.asyncio
async def test_wallet_client_returns_none_for_rpc_error(httpx_mock):
    httpx_mock.add_response(
        url="https://mainnet.helius-rpc.com/?api-key=secret",
        json={"jsonrpc": "2.0", "error": {"code": -32000, "message": "bad wallet"}},
    )

    async with httpx.AsyncClient() as http_client:
        snapshot = await WalletClient(http_client, "secret").snapshot("wallet-1")

    assert snapshot is None


@pytest.mark.asyncio
async def test_wallet_client_returns_sol_and_paginated_das_fungible_assets(httpx_mock):
    url = "https://mainnet.helius-rpc.com/?api-key=secret"
    httpx_mock.add_response(url=url, json={"result": {"value": 2_500_000_000}})
    httpx_mock.add_response(
        url=url,
        json={
            "result": {"items": [
                {"id": "mint-1", "interface": "FungibleToken", "content": {"metadata": {"symbol": "ONE"}}, "token_info": {"balance": 4.5, "price_info": {"price_per_token": 1.25}}},
                {"id": "usdc-mint", "interface": "FungibleToken", "content": {"metadata": {"symbol": "USDC"}}, "token_info": {"balance": 1_000_000, "decimals": 6}},
                {"id": "zero", "interface": "FungibleToken", "token_info": {"balance": 0}},
                {"id": "nft", "interface": "V1_NFT", "token_info": {"balance": 1}},
            ], "total": 1001}
        },
    )
    httpx_mock.add_response(
        url=url,
        json={"result": {"items": [
            {"id": "mint-2", "interface": "FungibleAsset", "content": {"metadata": {}}, "token_info": {"balance": 2}},
        ], "total": 1001}},
    )

    async with httpx.AsyncClient() as http_client:
        snapshot = await WalletClient(http_client, "secret").snapshot("wallet-1")

    assert snapshot is not None
    assert snapshot.address == "wallet-1"
    assert [(asset.mint_address, asset.symbol, asset.quantity) for asset in snapshot.assets] == [
        (None, "SOL", 2.5),
        ("mint-1", "ONE", 4.5),
        ("usdc-mint", "USDC", 1.0),
        ("mint-2", "mint-2", 2.0),
    ]
    assert snapshot.assets[1].price_usd == 1.25
    assert snapshot.total_usd is None
    assert snapshot.error is None
    assert snapshot.partial is False
    first_das_request = json.loads(httpx_mock.get_requests()[1].content)
    assert first_das_request["params"] == {
        "ownerAddress": "wallet-1",
        "page": 1,
        "limit": 1000,
        "displayOptions": {"showFungible": True},
    }


@pytest.mark.asyncio
async def test_wallet_client_keeps_first_page_when_later_das_page_fails(httpx_mock):
    url = "https://mainnet.helius-rpc.com/?api-key=secret"
    httpx_mock.add_response(url=url, json={"result": {"value": 1_000_000_000}})
    httpx_mock.add_response(url=url, json={"result": {"items": [
        {"id": "mint-1", "interface": "FungibleToken", "token_info": {"balance": 1}},
    ], "total": 1001}})
    httpx_mock.add_response(url=url, status_code=503)

    async with httpx.AsyncClient() as http_client:
        snapshot = await WalletClient(http_client, "secret").snapshot("wallet-1")

    assert snapshot is not None
    assert [asset.mint_address for asset in snapshot.assets] == [None, "mint-1"]
    assert snapshot.partial is True


@pytest.mark.asyncio
async def test_wallet_client_marks_partial_when_das_page_cap_has_more_results(httpx_mock):
    url = "https://mainnet.helius-rpc.com/?api-key=secret"
    httpx_mock.add_response(url=url, json={"result": {"value": 0}})
    httpx_mock.add_response(url=url, json={"result": {"items": [], "total": 1001}})

    async with httpx.AsyncClient() as http_client:
        snapshot = await WalletClient(http_client, "secret", max_das_pages=1).snapshot("wallet-1")

    assert snapshot is not None
    assert snapshot.partial is True


@pytest.mark.asyncio
async def test_wallet_client_returns_none_when_first_das_page_fails(httpx_mock):
    url = "https://mainnet.helius-rpc.com/?api-key=secret"
    httpx_mock.add_response(url=url, json={"result": {"value": 1_000_000_000}})
    httpx_mock.add_response(url=url, status_code=503)

    async with httpx.AsyncClient() as http_client:
        snapshot = await WalletClient(http_client, "secret").snapshot("wallet-1")

    assert snapshot is None


@pytest.mark.asyncio
async def test_wallet_service_persists_page_capped_snapshot_and_exposes_partial_api(httpx_mock, tmp_path):
    url = "https://mainnet.helius-rpc.com/?api-key=secret"
    httpx_mock.add_response(url=url, json={"result": {"value": 0}})
    httpx_mock.add_response(url=url, json={"result": {"items": [], "total": 1001}})
    repo = Repository(tmp_path / "signals.sqlite3")

    async with httpx.AsyncClient() as http_client:
        snapshot = await WalletService(
            repo, WalletClient(http_client, "secret", max_das_pages=1), PriceStub({})
        ).sample("wallet-1", datetime(2026, 7, 30, tzinfo=UTC))

    assert snapshot.partial is True
    assert repo.latest_wallet_snapshot() is not None
    assert repo.latest_wallet_snapshot().partial is True
    with TestClient(create_app(repo)) as test_client:
        assert test_client.get("/api/wallet").json()["partial"] is True
        assert test_client.get("/api/dashboard").json()["wallet"]["partial"] is True


@pytest.mark.asyncio
async def test_dex_prices_keeps_missing_token_as_none(httpx_mock):
    httpx_mock.add_response(
        url="https://api.dexscreener.com/tokens/v1/solana/mint-1",
        json=[{"baseToken": {"address": "mint-1"}, "priceUsd": "1.25"}],
    )
    httpx_mock.add_response(
        url="https://api.dexscreener.com/tokens/v1/solana/mint-2",
        json=[],
    )

    async with httpx.AsyncClient() as http_client:
        prices = await DexPriceClient(http_client).prices(("mint-1", "mint-2"))

    assert prices == {"mint-1": 1.25, "mint-2": None}


@pytest.mark.asyncio
async def test_dex_prices_keeps_other_mint_price_when_one_request_fails(httpx_mock):
    httpx_mock.add_response(
        url="https://api.dexscreener.com/tokens/v1/solana/mint-1",
        status_code=503,
    )
    httpx_mock.add_response(
        url="https://api.dexscreener.com/tokens/v1/solana/mint-2",
        json=[{"baseToken": {"address": "mint-2"}, "priceUsd": "2.50"}],
    )

    async with httpx.AsyncClient() as http_client:
        prices = await DexPriceClient(http_client).prices(("mint-1", "mint-2"))

    assert prices == {"mint-1": None, "mint-2": 2.5}


@pytest.mark.asyncio
async def test_dex_prices_keeps_other_mint_price_when_one_payload_lacks_price(httpx_mock):
    httpx_mock.add_response(
        url="https://api.dexscreener.com/tokens/v1/solana/mint-1",
        json=[{"baseToken": {"address": "mint-1"}}],
    )
    httpx_mock.add_response(
        url="https://api.dexscreener.com/tokens/v1/solana/mint-2",
        json=[{"baseToken": {"address": "mint-2"}, "priceUsd": "2.50"}],
    )

    async with httpx.AsyncClient() as http_client:
        prices = await DexPriceClient(http_client).prices(("mint-1", "mint-2"))

    assert prices == {"mint-1": None, "mint-2": 2.5}


class BalanceStub:
    def __init__(self, snapshot: WalletSnapshot | None) -> None:
        self.snapshot_value = snapshot
        self.addresses: list[str | None] = []

    async def snapshot(self, address: str | None) -> WalletSnapshot | None:
        self.addresses.append(address)
        return self.snapshot_value


class PriceStub:
    def __init__(self, values: dict[str, float | None]) -> None:
        self.values = values
        self.requests: list[tuple[str, ...]] = []

    async def prices(self, mints: tuple[str, ...]) -> dict[str, float | None]:
        self.requests.append(mints)
        return self.values


@pytest.mark.asyncio
async def test_wallet_service_persists_missing_address_error(tmp_path):
    repo = Repository(tmp_path / "signals.sqlite3")
    balances = BalanceStub(None)
    prices = PriceStub({})

    snapshot = await WalletService(repo, balances, prices).sample("   ", datetime(2026, 7, 30, tzinfo=UTC))

    assert snapshot.error == "wallet address not configured"
    assert snapshot.address is None
    assert repo.latest_wallet_snapshot() == snapshot
    assert balances.addresses == []
    assert prices.requests == []


@pytest.mark.asyncio
async def test_wallet_service_persists_unavailable_balance_error(tmp_path):
    repo = Repository(tmp_path / "signals.sqlite3")
    balances = BalanceStub(None)

    snapshot = await WalletService(repo, balances, PriceStub({})).sample("wallet-1", datetime(2026, 7, 30, tzinfo=UTC))

    assert snapshot.error == "wallet data unavailable"
    assert snapshot.address == "wallet-1"
    assert repo.latest_wallet_snapshot() == snapshot
    assert balances.addresses == ["wallet-1"]


@pytest.mark.asyncio
async def test_wallet_service_keeps_assets_without_price_and_sorts_by_usd(tmp_path):
    repo = Repository(tmp_path / "signals.sqlite3")
    raw = WalletSnapshot(
        "wallet-1",
        (WalletAsset("mint-a", "AAA", 2.0, None, None), WalletAsset("mint-b", "BBB", 3.0, None, None)),
        None,
        datetime(2000, 1, 1, tzinfo=UTC),
        None,
    )
    prices = PriceStub({"mint-a": None, "mint-b": 1.115})

    snapshot = await WalletService(repo, BalanceStub(raw), prices).sample("wallet-1", datetime(2026, 7, 30, tzinfo=UTC))

    assert prices.requests == [("mint-a", "mint-b")]
    assert snapshot.assets == (
        WalletAsset("mint-b", "BBB", 3.0, 1.115, 3.35),
        WalletAsset("mint-a", "AAA", 2.0, None, None),
    )
    assert snapshot.total_usd == 3.35
    assert snapshot.error is None


@pytest.mark.asyncio
async def test_wallet_service_values_native_sol_with_wrapped_sol_price(tmp_path):
    repo = Repository(tmp_path / "signals.sqlite3")
    raw = WalletSnapshot(
        "wallet-1", (WalletAsset(None, "SOL", 1.0, None, None),), None,
        datetime(2000, 1, 1, tzinfo=UTC), None,
    )
    wrapped_sol_mint = "So11111111111111111111111111111111111111112"
    prices = PriceStub({wrapped_sol_mint: 150.25})

    snapshot = await WalletService(repo, BalanceStub(raw), prices).sample(
        "wallet-1", datetime(2026, 7, 30, tzinfo=UTC)
    )

    assert prices.requests == [(wrapped_sol_mint,)]
    assert snapshot.assets == (WalletAsset(None, "SOL", 1.0, 150.25, 150.25),)
    assert snapshot.total_usd == 150.25
