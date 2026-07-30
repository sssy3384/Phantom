"""Read-only public wallet balance and price clients."""

from datetime import UTC, datetime

import httpx
import pytest

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
async def test_wallet_client_returns_sol_and_nonzero_spl_assets(httpx_mock):
    url = "https://mainnet.helius-rpc.com/?api-key=secret"
    httpx_mock.add_response(url=url, json={"result": {"value": 2_500_000_000}})
    httpx_mock.add_response(
        url=url,
        json={
            "result": {
                "value": [
                    {
                        "account": {
                            "data": {
                                "parsed": {
                                    "info": {
                                        "mint": "mint-1",
                                        "tokenAmount": {"uiAmount": 4.5},
                                    }
                                }
                            }
                        }
                    },
                    {
                        "account": {
                            "data": {
                                "parsed": {
                                    "info": {
                                        "mint": "empty-mint",
                                        "tokenAmount": {"uiAmount": 0},
                                    }
                                }
                            }
                        }
                    },
                ]
            }
        },
    )

    async with httpx.AsyncClient() as http_client:
        snapshot = await WalletClient(http_client, "secret").snapshot("wallet-1")

    assert snapshot is not None
    assert snapshot.address == "wallet-1"
    assert [(asset.mint_address, asset.symbol, asset.quantity) for asset in snapshot.assets] == [
        (None, "SOL", 2.5),
        ("mint-1", "mint-1", 4.5),
    ]
    assert snapshot.total_usd is None
    assert snapshot.error is None


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
