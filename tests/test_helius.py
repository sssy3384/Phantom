import httpx
import pytest

try:
    from golden_dog.clients.helius import HeliusClient
except ModuleNotFoundError:
    HeliusClient = None


@pytest.mark.asyncio
async def test_helius_returns_none_without_api_key():
    assert HeliusClient is not None

    async with httpx.AsyncClient() as http_client:
        enrichment = await HeliusClient(http_client, api_key=None).enrich("token-1")

    assert enrichment is None


@pytest.mark.asyncio
async def test_helius_parses_authorities_and_top_ten_concentration(httpx_mock):
    assert HeliusClient is not None
    url = "https://mainnet.helius-rpc.com/?api-key=test-key"
    httpx_mock.add_response(
        url=url,
        json={"result": {"value": {"data": {"parsed": {"info": {"mintAuthority": None, "freezeAuthority": None}}}}}},
    )
    httpx_mock.add_response(url=url, json={"result": {"value": [{"amount": "200"}, {"amount": "100"}]}})
    httpx_mock.add_response(url=url, json={"result": {"value": {"amount": "1000"}}})

    async with httpx.AsyncClient() as http_client:
        enrichment = await HeliusClient(http_client, api_key="test-key").enrich("token-1")

    assert enrichment.mint_authority is False
    assert enrichment.freeze_authority is False
    assert enrichment.top10_holder_pct == 30
