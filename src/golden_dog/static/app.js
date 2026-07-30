const escapeHtml = (value) => String(value ?? "-").replace(/[&<>"']/g, (char) => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[char]));

const statusText = (status) => ({healthy: "正常", stale: "延迟", failed: "失败", running: "运行中", stopped: "已停止"}[status] || status || "未知");
const sourceStatusTitle = "数据源状态";

function renderWallet(wallet) {
  document.querySelector("#wallet").innerHTML = `<div class="panel"><b>${escapeHtml(wallet.total_usd == null ? "估值不可用" : `$${wallet.total_usd}`)}</b><span>采样时间：${escapeHtml(wallet.sampled_at)}</span><span>状态：${escapeHtml(wallet.error || "正常")}</span></div>`;
  const assetRows = wallet.assets.map((asset) =>
    `<tr><td>${escapeHtml(asset.symbol)}</td><td>${escapeHtml(asset.quantity)}</td><td>${escapeHtml(asset.price_usd)}</td><td>${escapeHtml(asset.usd_value)}</td></tr>`
  ).join("");
  document.querySelector("#assets").innerHTML = assetRows
    ? `<div class="asset-table"><table><thead><tr><th>资产</th><th>数量</th><th>单价（USD）</th><th>估值（USD）</th></tr></thead><tbody>${assetRows}</tbody></table></div>`
    : `<p>${escapeHtml(wallet.error || "暂无钱包资产")}</p>`;
}

function renderRuntime(runtime, sources) {
  const helius = sources.find((source) => source.source === "helius");
  document.querySelector("#runtime").innerHTML = [
    ["运行状态", statusText(runtime.state)], ["扫描间隔", `${runtime.interval_seconds ?? "-"} 秒`],
    ["最近成功", runtime.last_success_at], ["下一次扫描", runtime.next_scan_at],
    ["钱包采样", runtime.wallet_error || "正常"],
    ["Helius", helius ? statusText(helius.status) : "暂无样本"],
    ["Bark 配置", runtime.bark.configuration],
    ["Bark 投递", runtime.bark.delivery_status || "暂无投递数据"],
    ["Bark 最近投递", runtime.bark.last_delivery_at], ["数据库", runtime.database.status],
  ].map(([label, value]) => `<span class="health"><b>${escapeHtml(label)}</b>：${escapeHtml(value)}</span>`).join("");
}

async function loadDashboard() {
  const response = await fetch("/api/dashboard");
  if (!response.ok) throw new Error("面板不可用");
  const dashboard = await response.json();
  document.querySelector("#summary").innerHTML = Object.entries(dashboard.today)
    .map(([key, value]) => `<span class="summary-item"><b>${escapeHtml({total:"总数", alerted:"提醒", watch:"观察", rejected:"拒绝"}[key] || key)}</b> ${escapeHtml(value)}</span>`).join("");
  renderWallet(dashboard.wallet);
  renderRuntime(dashboard.runtime, dashboard.health.sources);
  document.querySelector("#signals").innerHTML = dashboard.signals.map((signal) =>
    `<button class="card" type="button" data-pool="${escapeHtml(signal.pool_address)}"><b>${escapeHtml(signal.pool_address)}</b><span>评分 ${escapeHtml(signal.score)}</span><span>${escapeHtml(signal.status)}</span></button>`
  ).join("") || "<p>暂无合格信号。</p>";
  document.querySelectorAll("[data-pool]").forEach((card) => {
    card.addEventListener("click", () => loadSignalDetail(card.dataset.pool));
  });
  document.querySelector("#health").innerHTML = dashboard.health.sources.map((source) =>
    `<span class="health ${escapeHtml(source.status)}"><b>${escapeHtml(source.source)}</b>：${escapeHtml(statusText(source.status))}<br>采样：${escapeHtml(source.sampled_at)}<br>错误：${escapeHtml(source.error || "无")}</span>`
  ).join("") || `<p>${sourceStatusTitle}：暂无样本。</p>`;
  if (dashboard.signals[0]) await loadSignalDetail(dashboard.signals[0].pool_address);
}

async function loadSignalDetail(poolAddress) {
  const detail = document.querySelector("#detail");
  const response = await fetch(`/api/signals/${encodeURIComponent(poolAddress)}`);
  if (!response.ok) throw new Error("信号详情不可用");
  const signal = await response.json();
  const advice = signal.advice
    ? `入场上限：$${escapeHtml(signal.advice.entry_ceiling_usd)} · 最大仓位：${escapeHtml(signal.advice.max_position_pct)}% · 止损：${escapeHtml(signal.advice.stop_loss_pct)}% · 止盈：${escapeHtml(signal.advice.take_profit_pcts.join(" / "))}% · 失效条件：${escapeHtml(signal.advice.invalidation)}`
    : "暂无交易建议。";
  detail.innerHTML = `<article class="detail-card"><h3>${escapeHtml(signal.pool_address)}</h3><h4>评分证据</h4><ul>${signal.reasons.map((reason) => `<li>${escapeHtml(reason)}</li>`).join("")}</ul><h4>风险标记</h4><ul>${signal.risk_flags.map((flag) => `<li>${escapeHtml(flag)}</li>`).join("") || "<li>无</li>"}</ul><h4>建议</h4><p>${advice}</p><a href="${escapeHtml(signal.dexscreener_url)}" target="_blank" rel="noopener noreferrer">在 DexScreener 查看</a></article>`;
}

loadDashboard().catch((error) => { document.querySelector("#signals").textContent = error.message; });
