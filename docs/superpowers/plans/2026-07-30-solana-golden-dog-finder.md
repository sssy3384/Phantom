# Solana Golden Dog Finder Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local, read-only Solana new-pool signal service with a browser dashboard, Bark alerts, and copyable human trade suggestions.

**Architecture:** Poll DexScreener and GeckoTerminal for discovery, then enrich candidates through Helius. Persist immutable snapshots and scoring decisions in SQLite. A FastAPI server exposes JSON and static dashboard assets; a scheduler runs scans and sends only deduplicated high-quality alerts.

**Tech Stack:** Python 3.14, FastAPI, Uvicorn, httpx, SQLite, pytest, vanilla HTML/CSS/JS.

---

## File structure

- `pyproject.toml`: pinned runtime/test dependencies and commands.
- `.gitignore`, `.env.example`: local environment and credential safety.
- `src/golden_dog/models.py`: typed candidate, evidence, decision, and alert payloads.
- `src/golden_dog/config.py`: validated environment configuration and adjustable scoring settings.
- `src/golden_dog/clients/`: typed DexScreener, GeckoTerminal, and Helius HTTP clients.
- `src/golden_dog/repository.py`: SQLite schema, snapshot persistence, alert deduplication, health state.
- `src/golden_dog/scoring.py`: hard gates, deterministic dimension scoring, trade-advice construction.
- `src/golden_dog/service.py`: scan orchestration and stale-data behavior.
- `src/golden_dog/notifier.py`: Bark payload creation and delivery.
- `src/golden_dog/api.py`, `src/golden_dog/main.py`: HTTP routes, static files, scheduler lifecycle.
- `src/golden_dog/static/`: dashboard shell, rendering, and styles.
- `tests/`: fixtures and behavior-focused unit/integration tests.

### Task 1: Bootstrap a safe, testable Python application

**Files:**
- Create: `pyproject.toml`, `.gitignore`, `.env.example`, `src/golden_dog/__init__.py`, `tests/test_config.py`

- [ ] **Step 1: Write the failing configuration test**

```python
from golden_dog.config import Settings

def test_settings_rejects_missing_helius_key(monkeypatch):
    monkeypatch.delenv("HELIUS_API_KEY", raising=False)
    monkeypatch.setenv("BARK_BASE_URL", "https://api.day.app")
    assert Settings.from_env().helius_api_key is None
    assert Settings.from_env().bark_base_url == "https://api.day.app"
```

- [ ] **Step 2: Create project metadata and run the test red**

```toml
[project]
name = "golden-dog-finder"
version = "0.1.0"
requires-python = ">=3.14"
dependencies = ["fastapi>=0.116,<1", "uvicorn>=0.35,<1", "httpx>=0.28,<1"]

[dependency-groups]
dev = ["pytest>=8.4,<9", "pytest-asyncio>=1.1,<2", "pytest-httpx>=0.35,<1"]

[tool.pytest.ini_options]
pythonpath = ["src"]
```

Run: `python3 -m venv .venv && .venv/bin/pip install -e '.[dev]' && .venv/bin/pytest tests/test_config.py -v`

Expected: FAIL because `golden_dog.config` does not exist.

- [ ] **Step 3: Add minimal settings implementation**

```python
@dataclass(frozen=True)
class Settings:
    helius_api_key: str | None
    bark_base_url: str
    bark_device_key: str | None
    database_path: Path
    scan_interval_seconds: int = 30
    alert_score: int = 85

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            helius_api_key=os.getenv("HELIUS_API_KEY") or None,
            bark_base_url=os.getenv("BARK_BASE_URL", "https://api.day.app").rstrip("/"),
            bark_device_key=os.getenv("BARK_DEVICE_KEY") or None,
            database_path=Path(os.getenv("DATABASE_PATH", "data/golden_dog.sqlite3")),
        )
```

- [ ] **Step 4: Add credential-safe ignore/example files and verify green**

```
# .gitignore
.venv/
.env
data/*.sqlite3
__pycache__/
.pytest_cache/
```

```
# .env.example
HELIUS_API_KEY=
BARK_BASE_URL=https://api.day.app
BARK_DEVICE_KEY=
DATABASE_PATH=data/golden_dog.sqlite3
```

Run: `.venv/bin/pytest tests/test_config.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

Run: `git add pyproject.toml .gitignore .env.example src/golden_dog tests/test_config.py && git commit -m 'chore: bootstrap signal service'`

### Task 2: Define models and SQLite persistence

**Files:**
- Create: `src/golden_dog/models.py`, `src/golden_dog/repository.py`, `tests/test_repository.py`

- [ ] **Step 1: Write failing persistence tests**

```python
def test_repository_keeps_snapshot_and_suppresses_duplicate_alert(tmp_path):
    repo = Repository(tmp_path / "signals.sqlite3")
    repo.initialize()
    repo.save_decision(sample_decision("pool-1", score=90))
    assert repo.top_signals(limit=3)[0].pool_address == "pool-1"
    assert repo.claim_alert("pool-1", now=1_000) is True
    assert repo.claim_alert("pool-1", now=1_001) is False
```

- [ ] **Step 2: Run test red**

Run: `.venv/bin/pytest tests/test_repository.py::test_repository_keeps_snapshot_and_suppresses_duplicate_alert -v`

Expected: FAIL because `Repository` is missing.

- [ ] **Step 3: Implement focused immutable models and schema**

```python
@dataclass(frozen=True)
class Candidate:
    pool_address: str
    token_address: str
    symbol: str
    discovered_at: datetime
    pool_created_at: datetime
    liquidity_usd: float
    volume_m5_usd: float
    buys_m5: int
    sells_m5: int
    price_change_m5_pct: float

@dataclass(frozen=True)
class Decision:
    pool_address: str
    score: int
    status: Literal["rejected", "watch", "alerted"]
    reasons: tuple[str, ...]
    advice: TradeAdvice | None
    observed_at: datetime
```

Create tables `snapshots`, `decisions`, `alerts`, and `source_health`; use `pool_address` plus `observed_at` as each history record's natural key. `claim_alert` must atomically reject a record whose `last_sent_at` is less than six hours old.

- [ ] **Step 4: Run repository tests green**

Run: `.venv/bin/pytest tests/test_repository.py -v`

Expected: PASS, including six-hour deduplication and top-three ordering by score then recency.

- [ ] **Step 5: Commit**

Run: `git add src/golden_dog/models.py src/golden_dog/repository.py tests/test_repository.py && git commit -m 'feat: persist signal decisions'`

### Task 3: Discover and normalize new pools with resilient clients

**Files:**
- Create: `src/golden_dog/clients/base.py`, `src/golden_dog/clients/dexscreener.py`, `src/golden_dog/clients/geckoterminal.py`, `tests/fixtures/dexscreener_profiles.json`, `tests/fixtures/geckoterminal_pools.json`, `tests/test_clients.py`

- [ ] **Step 1: Write failing normalization and timeout tests**

```python
async def test_dexscreener_returns_only_solana_candidates(httpx_mock):
    httpx_mock.add_response(json=load_fixture("dexscreener_profiles.json"))
    candidates = await DexScreenerClient(http_client).discover()
    assert {item.pool_address for item in candidates} == {"sol-pool-1"}

async def test_client_records_timeout_as_source_error(httpx_mock):
    httpx_mock.add_exception(httpx.TimeoutException("slow"))
    result = await GeckoTerminalClient(http_client).discover()
    assert result.error == "timeout"
```

- [ ] **Step 2: Run tests red**

Run: `.venv/bin/pytest tests/test_clients.py -v`

Expected: FAIL because source clients are missing.

- [ ] **Step 3: Implement a shared eight-second client and normalizers**

`SourceResult` must contain `items`, `sampled_at`, `source`, and optional `error`. Construct one `httpx.AsyncClient(timeout=httpx.Timeout(8.0))`. DexScreener uses its Solana profiles/boosts then token-pair endpoints; GeckoTerminal uses Solana `new_pools`. Discard cross-chain, missing-address, duplicate, and non-USD-liquidity payloads.

- [ ] **Step 4: Run tests green**

Run: `.venv/bin/pytest tests/test_clients.py -v`

Expected: PASS. Add fixtures for malformed payload and HTTP 429; both return a `SourceResult.error` rather than raising into the scheduler.

- [ ] **Step 5: Commit**

Run: `git add src/golden_dog/clients tests && git commit -m 'feat: discover Solana pools'`

### Task 4: Enrich candidates and enforce deterministic risk scoring

**Files:**
- Create: `src/golden_dog/clients/helius.py`, `src/golden_dog/scoring.py`, `tests/test_scoring.py`

- [ ] **Step 1: Write failing hard-gate tests**

```python
@pytest.mark.parametrize("enrichment, reason", [
    (Enrichment(mint_authority=True), "mint authority enabled"),
    (Enrichment(freeze_authority=True), "freeze authority enabled"),
    (Enrichment(top10_holder_pct=56), "top 10 holders exceed 55%"),
])
def test_hard_gates_reject_unsafe_candidate(enrichment, reason):
    decision = score(sample_candidate(liquidity_usd=20_000), enrichment, now=NOW)
    assert decision.status == "rejected"
    assert reason in decision.reasons
```

- [ ] **Step 2: Run test red**

Run: `.venv/bin/pytest tests/test_scoring.py -v`

Expected: FAIL because `score` is missing.

- [ ] **Step 3: Implement Helius enrichment and scoring contracts**

Define `Enrichment(mint_authority, freeze_authority, top10_holder_pct, sampled_at)`. When `HELIUS_API_KEY` is absent or Helius errors, return no enrichment and force a rejected decision with `"critical enrichment unavailable"`. Reject pool age above four hours, liquidity below 10,000 USD, unavailable/older-than-three-minute enrichment, and every specified authority/concentration gate. Award exactly 25/25/20/15/15 maximum points for liquidity-growth, trade-acceleration, holder-distribution, price-momentum, and social-boost evidence. Attach every dimension's score in `reasons`.

- [ ] **Step 4: Add boundary tests and run green**

```python
def test_score_at_85_is_alerted_and_includes_advice():
    decision = score(sample_candidate(), safe_enrichment(), now=NOW)
    assert decision.score == 85
    assert decision.status == "alerted"
    assert decision.advice.max_position_pct == 5
```

Run: `.venv/bin/pytest tests/test_scoring.py -v`

Expected: PASS for every gate and score boundary.

- [ ] **Step 5: Commit**

Run: `git add src/golden_dog/clients/helius.py src/golden_dog/scoring.py tests/test_scoring.py && git commit -m 'feat: score and gate pool signals'`

### Task 5: Build advice, Bark delivery, and scan orchestration

**Files:**
- Create: `src/golden_dog/notifier.py`, `src/golden_dog/service.py`, `tests/test_service.py`, `tests/test_notifier.py`

- [ ] **Step 1: Write failing orchestration tests**

```python
async def test_scan_sends_one_bark_alert_for_new_high_quality_signal():
    service = SignalService(repository, sources=[source], scorer=scorer, notifier=notifier)
    await service.scan_once(now=NOW)
    notifier.send.assert_awaited_once()
    assert repository.claim_alert("pool-1", now=NOW.timestamp()) is False

async def test_stale_critical_source_stops_alerting():
    await service.scan_once(now=NOW)
    assert notifier.send.await_count == 0
```

- [ ] **Step 2: Run tests red**

Run: `.venv/bin/pytest tests/test_service.py tests/test_notifier.py -v`

Expected: FAIL because service and notifier are missing.

- [ ] **Step 3: Implement advice and notification behavior**

`TradeAdvice` must contain `entry_ceiling_usd`, `max_position_pct=5`, `invalidation`, `stop_loss_pct=-15`, and `take_profit_pcts=(25, 50)`. `SignalService.scan_once` must merge sources by pool address, persist source health and all decisions, and alert only the three highest newly qualifying pools per calendar day. `BarkNotifier` posts JSON to `{BARK_BASE_URL}/{BARK_DEVICE_KEY}` with title, score, reasons, risks, advice, and local detail URL. If configured credentials are absent, record a skipped delivery without error.

- [ ] **Step 4: Run tests green**

Run: `.venv/bin/pytest tests/test_service.py tests/test_notifier.py -v`

Expected: PASS for top-three cap, six-hour dedupe, revocation notification, stale data suppression, and credential-free local mode.

- [ ] **Step 5: Commit**

Run: `git add src/golden_dog/notifier.py src/golden_dog/service.py tests/test_service.py tests/test_notifier.py && git commit -m 'feat: alert qualified pool signals'`

### Task 6: Expose an API and browser dashboard

**Files:**
- Create: `src/golden_dog/api.py`, `src/golden_dog/main.py`, `src/golden_dog/static/index.html`, `src/golden_dog/static/app.js`, `src/golden_dog/static/styles.css`, `tests/test_api.py`

- [ ] **Step 1: Write failing API tests**

```python
def test_dashboard_api_returns_health_top_signals_and_advice(client):
    response = client.get("/api/dashboard")
    body = response.json()
    assert response.status_code == 200
    assert set(body) == {"health", "today", "signals"}
    assert len(body["signals"]) <= 3

def test_signal_detail_exposes_reasons_and_advice(client):
    body = client.get("/api/signals/pool-1").json()
    assert body["advice"]["max_position_pct"] == 5
    assert body["reasons"]
```

- [ ] **Step 2: Run tests red**

Run: `.venv/bin/pytest tests/test_api.py -v`

Expected: FAIL because API routes are missing.

- [ ] **Step 3: Implement read-only routes and dashboard**

Expose `GET /api/health`, `GET /api/dashboard`, `GET /api/signals/{pool_address}`, and `GET /api/history`. Serve `/` from static assets. The page must visibly show stale/failed source status, summary counts, highest three cards, detail panel with all score evidence, risk flags, advice, and a DexScreener external link. The dashboard contains no wallet adapter, connect button, signing code, or order action.

- [ ] **Step 4: Run API and browser smoke tests**

Run: `.venv/bin/pytest tests/test_api.py -v && .venv/bin/uvicorn golden_dog.main:app --port 8080`

Expected: tests PASS; opening `http://127.0.0.1:8080` displays dashboard with fixture-backed data and no wallet prompts.

- [ ] **Step 5: Commit**

Run: `git add src/golden_dog/api.py src/golden_dog/main.py src/golden_dog/static tests/test_api.py && git commit -m 'feat: add signal dashboard'`

### Task 7: Add lifecycle controls, documentation, and final safety verification

**Files:**
- Create: `README.md`, `tests/test_integration.py`
- Modify: `src/golden_dog/main.py`

- [ ] **Step 1: Write failing lifecycle test**

```python
async def test_scheduler_uses_configured_interval(monkeypatch):
    calls, delays = [], []
    async def scan_once(): calls.append(1)
    async def fake_sleep(seconds):
        delays.append(seconds)
        raise asyncio.CancelledError
    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    with pytest.raises(asyncio.CancelledError):
        await run_scan_loop(scan_once, interval_seconds=30)
    assert calls == [1]
    assert delays == [30]
```

- [ ] **Step 2: Run test red**

Run: `.venv/bin/pytest tests/test_integration.py -v`

Expected: FAIL because scheduler lifecycle is missing.

- [ ] **Step 3: Implement lifecycle and README**

Start one background scan loop during FastAPI lifespan; log but continue after a scan failure; cancel and await it during shutdown. README must document venv setup, `.env` creation, `uvicorn` command, data-source requirements, Bark setup, score gates, and the explicit no-wallet/no-trading boundary.

- [ ] **Step 4: Run full verification and safety audit**

Run: `.venv/bin/pytest -v && rg -n -i 'private.?key|seed phrase|walletconnect|signTransaction|sendTransaction|order submit' src tests`

Expected: all tests PASS; search returns no matches in `src/` other than README safety text, and never returns a transaction implementation.

- [ ] **Step 5: Commit**

Run: `git add README.md src/golden_dog/main.py tests/test_integration.py && git commit -m 'docs: document safe signal service operation'`
