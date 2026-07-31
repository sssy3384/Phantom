# 全币种钱包余额 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 读取公开 Solana 钱包的全部 fungible 币种余额，并安全显示 SOL、Token-2022 和 DAS asset 的 USD 估值。

**Architecture:** `WalletClient` 保留 SOL 查询，新增 Helius DAS 分页 `getAssetsByOwner`。`WalletService` 合并 DAS/SOL、优先 DAS price 再回退 DexScreener，保存 `partial` 快照；API/中文 UI 显示不完整状态而不伪造完整性。

**Tech Stack:** Python 3.14、httpx、Helius JSON-RPC DAS、SQLite、FastAPI、pytest、vanilla JS。

---

### Task 1: 增加 DAS 全币种余额客户端

**Files:**
- Modify: `src/golden_dog/models.py`, `src/golden_dog/clients/wallet.py`, `tests/test_wallet.py`

- [ ] **Step 1: 写失败的 DAS 分页测试**

```python
@pytest.mark.asyncio
async def test_wallet_client_merges_das_fungible_pages_and_marks_page_limit(httpx_mock):
    httpx_mock.add_response(json={"result": {"value": 1_000_000_000}})
    httpx_mock.add_response(json={"result": {"items": [fungible("mint-a", "AAA", "2")], "total": 2}})
    httpx_mock.add_response(json={"result": {"items": [fungible("mint-b", "BBB", "3")], "total": 2}})
    snapshot = await WalletClient(http_client, "key", max_das_pages=2).snapshot("public-address")
    assert [(item.symbol, item.quantity) for item in snapshot.assets] == [("SOL", 1), ("AAA", 2), ("BBB", 3)]
    assert snapshot.partial is False
```

- [ ] **Step 2: 运行 RED**

Run: `.venv/bin/python -m pytest tests/test_wallet.py -v`

Expected: FAIL，因为 client 没有 DAS 分页和 `partial` 字段。

- [ ] **Step 3: 最小实现**

```python
@dataclass(frozen=True)
class WalletSnapshot:
    address: str | None
    assets: tuple[WalletAsset, ...]
    total_usd: float | None
    sampled_at: datetime
    error: str | None
    partial: bool = False
```

实现 `WalletClient._das_page(address, page)` 调用 `getAssetsByOwner`，参数包含 `{"ownerAddress": address, "page": page, "limit": 1000, "displayOptions": {"showFungible": True}}`。只解析 `interface == "FungibleToken"`、正 balance 的 item；从 `token_info.balance`、`content.metadata.symbol`、`token_info.price_info.price_per_token` 提取数量、符号、价格。`max_das_pages=10` 时仍有下一页则 `partial=True`；第一页失败返回 `None`，后续页失败保留已有 asset 并 `partial=True`。

- [ ] **Step 4: 运行 GREEN**

Run: `.venv/bin/python -m pytest tests/test_wallet.py -v`

Expected: PASS，覆盖两页、零余额/NFT 排除、Token-2022、页上限和后续页失败。

- [ ] **Step 5: 提交**

```bash
git add src/golden_dog/models.py src/golden_dog/clients/wallet.py tests/test_wallet.py
git commit -m 'feat: read DAS wallet assets'
```

### Task 2: 合并价格、迁移快照并暴露 partial API

**Files:**
- Modify: `src/golden_dog/wallet.py`, `src/golden_dog/repository.py`, `src/golden_dog/api.py`, `tests/test_wallet.py`, `tests/test_repository.py`, `tests/test_api.py`

- [ ] **Step 1: 写失败的价格与旧快照兼容测试**

```python
async def test_wallet_service_prefers_das_price_and_falls_back_to_dex():
    snapshot = await service.sample("public-address", now=NOW)
    assert snapshot.assets_by_symbol["AAA"].price_usd == 1.5
    assert snapshot.assets_by_symbol["BBB"].price_usd == 2.0

def test_old_wallet_snapshot_json_defaults_partial_to_false(tmp_path):
    repo = Repository(tmp_path / "signals.sqlite3")
    repo.initialize()
    insert_pre_partial_snapshot(repo)
    assert repo.latest_wallet_snapshot().partial is False
```

- [ ] **Step 2: 运行 RED**

Run: `.venv/bin/python -m pytest tests/test_wallet.py tests/test_repository.py -v`

Expected: FAIL，因为现有快照无 `partial`、价格路径不保留 DAS 价格。

- [ ] **Step 3: 最小实现**

`WalletService` 保留 DAS price，只有 `price_usd is None` 的 mint 才批量请求 `DexPriceClient`。`Repository.save_wallet_snapshot` 写 `partial`；`latest_wallet_snapshot` 和 `latest_successful_wallet_snapshot` 用 `payload.get("partial", False)` 读取旧 JSON。`_wallet_payload` 返回安全 `partial`，不返回原始 RPC 错误或 watched address。

- [ ] **Step 4: 运行 GREEN**

Run: `.venv/bin/python -m pytest tests/test_wallet.py tests/test_repository.py tests/test_api.py -v`

Expected: PASS，DAS 价格优先、Dex fallback、部分状态、旧 JSON 都正确。

- [ ] **Step 5: 提交**

```bash
git add src/golden_dog/wallet.py src/golden_dog/repository.py src/golden_dog/api.py tests/test_wallet.py tests/test_repository.py tests/test_api.py
git commit -m 'feat: persist partial all-token balances'
```

### Task 3: 中文 UI、文档和最终安全验证

**Files:**
- Modify: `src/golden_dog/static/app.js`, `src/golden_dog/static/styles.css`, `README.md`, `tests/test_api.py`

- [ ] **Step 1: 写失败的 partial UI 测试**

```python
def test_static_dashboard_explains_partial_wallet_data(client):
    script = client.get("/static/app.js").text
    assert "数据不完整" in script
    assert "mint" in script.lower()
```

- [ ] **Step 2: 运行 RED**

Run: `.venv/bin/python -m pytest tests/test_api.py::test_static_dashboard_explains_partial_wallet_data -v`

Expected: FAIL，因为 UI 未渲染 partial 和 mint。

- [ ] **Step 3: 最小实现**

资产表新增“Mint”列；当 `wallet.partial` 为真显示“数据不完整：已达到分页上限或上游响应中断”。README 说明 DAS 覆盖范围、NFT 不在币种估值内、估值不等于盈亏、缓存/分页限制与只读边界。

- [ ] **Step 4: 最终验证**

Run: `.venv/bin/python -m pytest -q && git diff --check && rg -n -i 'private.?key|seed phrase|walletconnect|signTransaction|sendTransaction|order submit|trade execution' src tests .env.example README.md`

Expected: 全部测试 PASS；搜索仅命中测试/README 的安全否定文本，无实现代码。

- [ ] **Step 5: 提交**

```bash
git add src/golden_dog/static README.md tests/test_api.py
git commit -m 'docs: explain all-token wallet monitoring'
```
