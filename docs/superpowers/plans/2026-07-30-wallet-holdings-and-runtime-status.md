# 钱包持仓与运行状态 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不连接钱包、不交易的前提下，显示一个公开 Solana 地址的资产数量、USD 估值和服务运行状态。

**Architecture:** `WalletClient` 从 Helius RPC 读取公开地址的 SOL/SPL 余额，`DexPriceClient` 只读补价格；`WalletService` 将一次结果保存为 SQLite 快照。扫描循环调用钱包采样并维护内存 runtime 状态；FastAPI 仅读取 SQLite/状态，中文 dashboard 轮询 API，不在 GET 请求中访问链或市场。

**Tech Stack:** Python 3.14、FastAPI、httpx、SQLite、pytest、vanilla HTML/CSS/JS。

---

## File structure

- `src/golden_dog/models.py`: 增加 immutable `WalletAsset`、`WalletSnapshot`、`RuntimeStatus`。
- `src/golden_dog/config.py`: 增加可选 `watch_wallet_address`。
- `src/golden_dog/clients/wallet.py`: Helius public-address balance client。
- `src/golden_dog/clients/prices.py`: DexScreener USD price client。
- `src/golden_dog/wallet.py`: 合并余额与价格的 `WalletService`。
- `src/golden_dog/repository.py`: 钱包快照持久化和最新快照读取。
- `src/golden_dog/runtime.py`: 不含密钥的扫描运行状态。
- `src/golden_dog/main.py`: 将 wallet scan 与 signal scan 编排进既有 lifecycle。
- `src/golden_dog/api.py`, `src/golden_dog/static/`: 只读 API 与中文 dashboard。
- `tests/test_wallet.py`, `tests/test_runtime.py`, `tests/test_api.py`: 端到端行为测试。

### Task 1: 定义只读钱包配置、模型和 Helius/Dex 客户端

**Files:**
- Modify: `src/golden_dog/config.py`, `src/golden_dog/models.py`, `src/golden_dog/clients/__init__.py`
- Create: `src/golden_dog/clients/wallet.py`, `src/golden_dog/clients/prices.py`, `tests/test_wallet.py`

- [ ] **Step 1: 写失败的配置与余额规范测试**

```python
@pytest.mark.asyncio
async def test_wallet_client_normalizes_sol_and_spl_balances(httpx_mock):
    httpx_mock.add_response(json={"result": {"value": 2_500_000_000}})
    httpx_mock.add_response(json={"result": {"value": [{"account": {"data": {"parsed": {"info": {
        "mint": "mint-a", "tokenAmount": {"uiAmount": 12.5, "uiAmountString": "12.5"}
    }}}}}]}})
    client = WalletClient(http_client, "helius-key")
    assets = await client.balances("6DRXtT3XegzpZE8YFsDgT3euv4UqseaN38MXfDYifkb8")
    assert [(asset.symbol, asset.quantity) for asset in assets] == [("SOL", 2.5), ("mint-a", 12.5)]

def test_settings_reads_optional_watch_wallet_address(monkeypatch):
    monkeypatch.setenv("WATCH_WALLET_ADDRESS", "wallet-public-address")
    assert Settings.from_env().watch_wallet_address == "wallet-public-address"
```

- [ ] **Step 2: 运行 RED**

Run: `.venv/bin/python -m pytest tests/test_wallet.py -v`

Expected: FAIL，因为 `WalletClient`、`WalletAsset` 与配置字段不存在。

- [ ] **Step 3: 添加最小 immutable 契约与输入验证**

```python
@dataclass(frozen=True)
class WalletAsset:
    mint_address: str | None
    symbol: str
    quantity: float
    price_usd: float | None
    usd_value: float | None

@dataclass(frozen=True)
class WalletSnapshot:
    address: str | None
    assets: tuple[WalletAsset, ...]
    total_usd: float | None
    sampled_at: datetime
    error: str | None
```

`WalletClient.balances()`：没有 Helius key 时返回 `None`；否则依次调用 `getBalance` 和 `getTokenAccountsByOwner`，只解析 `uiAmount > 0` 的 SPL 项。RPC/HTTP/JSON 错误必须返回 `None`，不得包含 URL/key 的错误字符串。`DexPriceClient.prices(mints)` 对每个 mint 调用既有 Solana token-pair endpoint，选择第一个可解析的 `priceUsd`；单一 token 失败只返回该 token 无价格。

- [ ] **Step 4: 运行 GREEN，并补充价格失败测试**

Run: `.venv/bin/python -m pytest tests/test_wallet.py -v`

Expected: PASS，覆盖缺 key、RPC 错误、零余额过滤和价格不可用。

- [ ] **Step 5: 提交**

```bash
git add src/golden_dog/config.py src/golden_dog/models.py src/golden_dog/clients tests/test_wallet.py
git commit -m 'feat: read public wallet balances'
```

### Task 2: 持久化并组装钱包快照

**Files:**
- Create: `src/golden_dog/wallet.py`
- Modify: `src/golden_dog/repository.py`, `tests/test_repository.py`, `tests/test_wallet.py`

- [ ] **Step 1: 写失败的快照测试**

```python
@pytest.mark.asyncio
async def test_wallet_service_keeps_balances_when_price_is_unavailable(tmp_path):
    service = WalletService(repo=Repository(tmp_path / "wallet.sqlite3"), balances=balances, prices=prices)
    snapshot = await service.sample("wallet-public-address", now=NOW)
    assert snapshot.assets[0].quantity == 2.5
    assert snapshot.assets[0].usd_value is None
    assert repo.latest_wallet_snapshot().error is None
```

- [ ] **Step 2: 运行 RED**

Run: `.venv/bin/python -m pytest tests/test_wallet.py::test_wallet_service_keeps_balances_when_price_is_unavailable -v`

Expected: FAIL，因为 `WalletService` 与钱包表不存在。

- [ ] **Step 3: 最小实现快照表与服务**

```python
CREATE TABLE IF NOT EXISTS wallet_snapshots (
    sampled_at TEXT PRIMARY KEY,
    address TEXT,
    payload_json TEXT NOT NULL,
    error TEXT
);
```

`Repository.save_wallet_snapshot(snapshot)` 保存完整 JSON；`latest_wallet_snapshot()` 按 `sampled_at DESC LIMIT 1` 还原 dataclass。`WalletService.sample(address, now)`：地址未配置→保存 `error="wallet address not configured"`；余额不可用→保存 `error="wallet data unavailable"`；否则以已知价格生成 `usd_value=round(quantity * price, 2)`、总额为已知资产之和，按 USD 倒序。

- [ ] **Step 4: 运行 GREEN**

Run: `.venv/bin/python -m pytest tests/test_wallet.py tests/test_repository.py -v`

Expected: PASS，覆盖地址缺失、余额失败、价格缺失、序列化往返与最新快照排序。

- [ ] **Step 5: 提交**

```bash
git add src/golden_dog/wallet.py src/golden_dog/repository.py tests/test_wallet.py tests/test_repository.py
git commit -m 'feat: persist wallet snapshots'
```

### Task 3: 编排钱包采样与无密钥运行状态

**Files:**
- Create: `src/golden_dog/runtime.py`, `tests/test_runtime.py`
- Modify: `src/golden_dog/main.py`

- [ ] **Step 1: 写失败的扫描状态与隔离测试**

```python
@pytest.mark.asyncio
async def test_runtime_records_wallet_failure_without_stopping_signal_scan():
    runtime = RuntimeState(interval_seconds=30)
    calls = []
    async def signal_scan(): calls.append("signal")
    async def wallet_scan(): raise RuntimeError("offline")
    await run_combined_scan(signal_scan, wallet_scan, runtime, now=NOW)
    assert calls == ["signal"]
    assert runtime.snapshot().wallet_error == "RuntimeError"
    assert runtime.snapshot().last_success_at == NOW
```

- [ ] **Step 2: 运行 RED**

Run: `.venv/bin/python -m pytest tests/test_runtime.py -v`

Expected: FAIL，因为 runtime 模块与组合扫描不存在。

- [ ] **Step 3: 最小实现运行状态与 lifecycle 接线**

```python
@dataclass(frozen=True)
class RuntimeStatus:
    running: bool
    interval_seconds: int
    last_started_at: datetime | None
    last_success_at: datetime | None
    last_failure_at: datetime | None
    wallet_error: str | None
```

`RuntimeState` 只在内存保存上述值，`snapshot()` 不包含地址、环境或 key。`create_default_app()` 创建 `WalletService`；`run_combined_scan()` 先运行 signal scan，再独立 try/except 钱包 sample，最后更新 success/failure。原有 `run_scan_loop()`、取消和 client shutdown 顺序不可改变。

- [ ] **Step 4: 运行 GREEN**

Run: `.venv/bin/python -m pytest tests/test_runtime.py tests/test_integration.py -v`

Expected: PASS，既有 lifecycle cancel/await 测试继续通过。

- [ ] **Step 5: 提交**

```bash
git add src/golden_dog/runtime.py src/golden_dog/main.py tests/test_runtime.py
git commit -m 'feat: report scanner runtime status'
```

### Task 4: 扩展只读 API 并将 dashboard 中文化

**Files:**
- Modify: `src/golden_dog/api.py`, `src/golden_dog/main.py`, `src/golden_dog/static/index.html`, `src/golden_dog/static/app.js`, `src/golden_dog/static/styles.css`, `tests/test_api.py`

- [ ] **Step 1: 写失败的 API 与中文 UI 测试**

```python
def test_dashboard_returns_wallet_and_runtime_without_secrets(client):
    body = client.get("/api/dashboard").json()
    assert set(body) == {"health", "today", "signals", "wallet", "runtime"}
    assert body["wallet"]["assets"][0]["usd_value"] == 100.0
    assert "HELIUS_API_KEY" not in repr(body)

def test_root_is_chinese_and_has_no_wallet_connect_or_order_actions(client):
    page = client.get("/").text
    assert "钱包持仓" in page and "系统状态" in page
    assert "connect wallet" not in page.lower() and "signTransaction" not in page
```

- [ ] **Step 2: 运行 RED**

Run: `.venv/bin/python -m pytest tests/test_api.py -v`

Expected: FAIL，因为 dashboard 尚未有 `wallet`、`runtime` 或中文标签。

- [ ] **Step 3: 最小实现只读 payload 与页面**

`create_app(repository, now=..., runtime=None)` 将 `runtime.snapshot()` 序列化；没有 runtime 时返回 `{running: false, interval_seconds: 0, ...}`。新增 `GET /api/wallet`，返回 `latest_wallet_snapshot()`，没有快照时显式 `error="wallet snapshot unavailable"`。

HTML 标题和 section 改为中文。JS 从 `/api/dashboard` 渲染：顶部“钱包总估值 / 运行状态 / 信号摘要”，持仓表（资产、数量、USD 估值、价格不可用），系统状态和每个数据源状态；保留信号卡、详情、风险、建议与 DexScreener link。所有插值继续经 `escapeHtml`。不添加 `<button>` 用于连接、签名、买卖或提交订单。

- [ ] **Step 4: 运行 GREEN 与本地 smoke**

Run: `.venv/bin/python -m pytest tests/test_api.py -v`

Expected: PASS。

Run: `WATCH_WALLET_ADDRESS=6DRXtT3XegzpZE8YFsDgT3euv4UqseaN38MXfDYifkb8 .venv/bin/python -m uvicorn golden_dog.main:app --host 127.0.0.1 --port 18080`

Expected: `GET /`、`GET /api/dashboard`、`GET /api/wallet` return 200；没有钱包连接或订单 UI。

- [ ] **Step 5: 提交**

```bash
git add src/golden_dog/api.py src/golden_dog/main.py src/golden_dog/static tests/test_api.py
git commit -m 'feat: show Chinese wallet dashboard'
```

### Task 5: 更新本地配置、文档和最终安全审计

**Files:**
- Modify: `.env.example`, `README.md`, `tests/test_config.py`

- [ ] **Step 1: 写失败的配置示例测试**

```python
def test_env_example_contains_only_public_wallet_setting():
    content = Path(".env.example").read_text()
    assert "WATCH_WALLET_ADDRESS=" in content
    assert "PRIVATE_KEY" not in content and "SEED" not in content
```

- [ ] **Step 2: 运行 RED**

Run: `.venv/bin/python -m pytest tests/test_config.py::test_env_example_contains_only_public_wallet_setting -v`

Expected: FAIL，因为 example 尚未声明公开地址。

- [ ] **Step 3: 更新文档与示例**

在 `.env.example` 增加空的 `WATCH_WALLET_ADDRESS=`。README 说明公开地址配置、余额/价格可能缺失、USD 是当前估值非盈亏、运行状态字段，以及只读/无私钥/无交易边界。

- [ ] **Step 4: 最终验证**

Run: `.venv/bin/python -m pytest -v && git diff --check && rg -n -i 'private.?key|seed phrase|walletconnect|signTransaction|sendTransaction|order submit|trade execution' src tests .env.example README.md`

Expected: 全部测试 PASS；搜索仅允许 README 中明确的“无此功能”安全说明，不能有实现代码命中。

- [ ] **Step 5: 提交**

```bash
git add .env.example README.md tests/test_config.py
git commit -m 'docs: explain read-only wallet monitoring'
```
