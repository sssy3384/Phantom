"""Read-only public wallet balance and price clients."""

from datetime import UTC, datetime

import httpx
import pytest

from golden_dog.clients.prices import DexPriceClient
from golden_dog.clients.wallet import WalletClient
from golden_dog.config import Settings
from golden_dog.models import WalletAsset, WalletSnapshot


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
