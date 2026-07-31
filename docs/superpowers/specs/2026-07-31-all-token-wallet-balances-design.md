# 全币种钱包余额设计

## 目标

将公开 Solana 地址的钱包余额从 legacy SPL/Token-2022 两类账户查询，升级为 Helius DAS 全量 fungible asset 查询；保留原生 SOL、USD 估值、只读边界和中文 dashboard。

## 只读与隐私边界

- 仅使用本地 `.env` 的 `HELIUS_API_KEY` 与 `WATCH_WALLET_ADDRESS`。
- 仅请求 `getBalance` 与 DAS `getAssetsByOwner`；不连接浏览器钱包，不签名，不发送交易。
- API、SQLite 和日志不得写入 API key；dashboard 不返回完整 watched address。

## 余额采集

1. 用 `getBalance` 读取原生 SOL。
2. 用 `getAssetsByOwner` 分页读取 DAS asset；请求 `showFungible=true`，每页最多 1000，顺序 page 从 1 开始。
3. 只采集可解析、数量大于零的 fungible asset。按 mint 合并，保留 symbol、decimals、数量和 metadata。
4. 默认最多读取 10 页；达到上限时保存 `partial=true` 和安全状态 `wallet data partial`，而不是伪称完整。
5. NFT、压缩 NFT 和没有 fungible balance 的 asset 不进入“币种余额”或 USD 总额；可在未来增加独立 NFT 摘要。

## 估值与失败语义

- 优先使用 DAS asset 的安全公开价格字段；缺价时调用既有 DexScreener price client。
- 单个价格失败仅让该资产显示“价格不可用”，不清空其他余额。
- 任一 DAS page 的 HTTP/RPC/解析失败停止后续 page，保留已读取资产，标记 `partial=true`；若第 1 页失败则维持现有“wallet data unavailable”语义。
- 原生 SOL 继续经 wSOL mint 获取 DexScreener 价格。

## 持久化与 API

`WalletSnapshot` 增加 `partial: bool`。SQLite JSON 持久化与旧快照反序列化兼容：缺字段默认 `false`。

`/api/wallet` 与 `/api/dashboard.wallet` 返回 `partial`；UI 在 `stale` 之外显示“数据不完整：已达到分页上限或上游响应中断”。资产表增加 mint 列，仍不显示 watched address。

## 验证

TDD 覆盖：DAS 两页合并、Token-2022 fungible、零余额/NFT 排除、分页上限 partial、第二页失败 partial、第一页失败 unavailable、DAS 价格优先/Dex fallback、JSON 旧快照兼容、API/中文 UI partial 提示。全量 pytest、数据库迁移、只读安全关键词审计和真实 API smoke 必须通过。
