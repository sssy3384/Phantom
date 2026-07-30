# Golden Dog Finder

Read-only Solana pool signal service. It discovers new pools, applies safety and
quality scoring, persists results, and can send Bark notifications. It does not
hold a wallet, trade, or submit orders.

## Run locally

```bash
python3 -m venv .venv
source .venv/bin/activate
uv sync --group dev
cp .env.example .env
set -a; source .env; set +a
uv run uvicorn golden_dog.main:app --host 127.0.0.1 --port 8000
```

Configure local environment variables before starting:

```dotenv
DATABASE_PATH=data/golden_dog.sqlite3
HELIUS_API_KEY=your_helius_api_key
BARK_BASE_URL=https://api.day.app
BARK_DEVICE_KEY=your_bark_device_key
```

`HELIUS_API_KEY` is needed for on-chain mint, freeze-authority, and holder
concentration enrichment. Without it, the critical enrichment gate rejects
candidates. Bark delivery is disabled when `BARK_DEVICE_KEY` is absent. Install
the Bark iOS app or run a compatible Bark server, then use its device key and
base URL.

## Data sources and gates

Discovery uses DexScreener and GeckoTerminal public APIs. Enrichment uses the
Helius Solana RPC API. Network availability and source freshness affect whether
signals are alertable.

Hard rejection gates: missing/stale critical enrichment, enabled mint or freeze
authority, top-10 holders above 55%, liquidity below $10,000, and pools older
than four hours. Remaining candidates receive liquidity, trading activity,
holder concentration, and momentum scores; score 85+ is alertable.

## Safety boundary

This service has no wallet adapter, private key, seed phrase handling,
`signTransaction`, `sendTransaction`, order-submission, or trade-execution
code. Signals and Bark notifications are informational only; verify
independently before making any decision.
