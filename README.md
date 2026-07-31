# Golden Dog Finder

只读 Solana 池子信号与钱包持仓监控服务。发现新池子、执行安全与质量评分、保存结果，并可发送 Bark 通知。

## 本地运行

```bash
python3 -m venv .venv
source .venv/bin/activate
uv sync --group dev
cp .env.example .env
set -a; source .env; set +a
uv run uvicorn golden_dog.main:app --host 127.0.0.1 --port 8000
```

启动前配置本地环境变量：

```dotenv
DATABASE_PATH=data/golden_dog.sqlite3
HELIUS_API_KEY=your_helius_api_key
BARK_BASE_URL=https://api.day.app
BARK_DEVICE_KEY=your_bark_device_key
WATCH_WALLET_ADDRESS=your_solana_public_address
```

`WATCH_WALLET_ADDRESS` 只填公开 Solana 地址；留空则不采集持仓。不得配置钱包连接、私钥、助记词、授权或交易凭据。

`HELIUS_API_KEY` 用于链上 mint、freeze authority 与持仓集中度补全；缺失时关键补全门槛会拒绝候选池。未设置 `BARK_DEVICE_KEY` 时不发送 Bark 通知。

钱包接口通过 Helius DAS `getAssetsByOwner` 读取 fungible 资产，并合并原生 SOL；NFT 不在持仓范围内。DAS 最多读取 10 页、每页 1000 个资产（上限 10,000）；达到分页上限或上游响应中断时，接口会标记数据不完整，页面不会把它误显示为数据延迟。数据延迟表示本次采样失败、展示最近成功持仓；数据不完整表示本次读取只得到部分资产，两者可同时出现。

钱包接口返回资产数量、公开 asset mint、可取得的当前 USD 估值、总 USD 估值与采样时间。价格优先使用资产附带报价，缺失时按 mint 查询价格回退；余额、代币映射或市场 USD 估值可能不可用。当前 USD 估值不是 PnL、成本、收益或投资建议。

运行状态字段：`state`、`running`、`interval_seconds`、`last_started_at`、`last_success_at`、`last_failure_at`、`wallet_error`。状态输出不会返回监听地址或任何凭据。

## 数据源与门槛

发现使用 DexScreener、GeckoTerminal 公共 API；补全使用 Helius Solana RPC API。网络可用性与数据源新鲜度会影响是否告警。

硬拒绝门槛：关键补全缺失或过期、mint 或 freeze authority 启用、前 10 持仓超过 55%、流动性低于 $10,000、池子超过四小时。其余候选按流动性、活跃度、持仓集中度与动量评分；85 分以上可告警。

## 安全边界

服务仅通过公开地址读取链上余额与公开市场价格，不接入钱包，不处理私钥、助记词或其他敏感钱包材料，不发起授权、交易、下单或资产操作。监控结果与 Bark 通知仅供信息参考，请独立核验后再作决定。
