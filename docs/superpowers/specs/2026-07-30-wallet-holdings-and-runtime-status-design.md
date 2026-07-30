# 钱包持仓与运行状态设计

## 目标

为 Golden Dog Finder 的中文 dashboard 增加一个只读公开 Solana 地址的持仓概览，以及服务运行状态。保留现有新池信号、评分、风险和 Bark 能力。

## 安全边界

- 仅接收 `WATCH_WALLET_ADDRESS` 公开地址；不连接浏览器钱包。
- 不读取、存储或传输私钥、助记词、签名请求或交易请求。
- 不发送交易、订单或资金转移。
- 地址 `6DRXtT3XegzpZE8YFsDgT3euv4UqseaN38MXfDYifkb8` 只保存在本地 `.env`，不提交。

## 数据与缓存

`WalletClient` 使用现有 Helius JSON-RPC client：获取原生 SOL 余额及 SPL token 余额。地址未配置、Helius key 缺失、RPC 失败或响应异常时，返回一个带错误状态的空快照，不中断新池扫描。

价格由 DexScreener 的 Solana token-pair endpoint 补充。资产始终保留数量；没有可用 USD 价格时 `usd_value` 为 `null` 并显示“价格不可用”。SOL 使用同样的只读市场价格路径。该功能不计算成本、收益或盈亏。

SQLite 保存每次钱包采样：地址、资产数量、USD 估值、总估值、采样时间和错误。API 使用最新快照，不在 HTTP GET 中触发 RPC 请求。

## 扫描与运行状态

每次既有扫描循环运行时，先采样数据源和新池信号，再采样钱包。某一阶段失败会写入独立 health 状态且不取消后续循环。

系统状态包含：扫描器是否运行、上次成功与失败时间、下一次扫描时间、配置扫描间隔、数据源、Helius、Bark 和数据库状态。该状态全部来自本地运行时状态与 SQLite，不暴露环境变量或密钥。

## API 与中文 dashboard

新增只读 `GET /api/wallet` 和扩展 `GET /api/dashboard`：返回 `wallet`、`runtime`、既有 `health`、`today` 和 `signals`。响应中不含任何 key、完整 secret 或交易指令。

页面中文化，布局确定为：

1. 顶部显示钱包总估值、运行状态和信号摘要。
2. 左侧显示资产符号、数量、USD 估值及钱包短地址。
3. 右侧显示扫描、数据源、Helius、Bark 和数据库状态。
4. 下方保留 Top 信号、评分证据、风险标记、建议和 DexScreener 外链。

数据缺失、地址未配置、价格不可用和来源失败必须可见，而不是显示伪造数字。

## 验证

TDD 覆盖：地址缺失、SOL/SPL 正常余额、价格缺失、客户端错误、快照持久化、运行状态时间线、API 序列化和中文静态页面。完整测试、API smoke、`git diff --check` 与钱包/交易关键词安全审计都必须通过。
