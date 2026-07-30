const escapeHtml = (value) => String(value).replace(/[&<>"]/g, (char) => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;"}[char]));

async function loadDashboard() {
  const response = await fetch("/api/dashboard");
  if (!response.ok) throw new Error("Dashboard unavailable");
  const dashboard = await response.json();
  document.querySelector("#summary").innerHTML = Object.entries(dashboard.today)
    .map(([key, value]) => `<span class="summary-item"><b>${escapeHtml(key)}</b> ${escapeHtml(value)}</span>`).join("");
  document.querySelector("#signals").innerHTML = dashboard.signals.map((signal) =>
    `<button class="card" type="button" data-pool="${escapeHtml(signal.pool_address)}"><b>${escapeHtml(signal.pool_address)}</b><span>Score ${escapeHtml(signal.score)}</span><span>${escapeHtml(signal.status)}</span></button>`
  ).join("") || "<p>No qualified signals.</p>";
  document.querySelectorAll("[data-pool]").forEach((card) => {
    card.addEventListener("click", () => loadSignalDetail(card.dataset.pool));
  });
  document.querySelector("#health").innerHTML = dashboard.health.sources.map((source) =>
    `<span class="health ${escapeHtml(source.status)}"><b>${escapeHtml(source.source)}</b>: ${escapeHtml(source.status)}<br>sampled: ${escapeHtml(source.sampled_at)}<br>error: ${escapeHtml(source.error || "none")}</span>`
  ).join("") || "<p>No source samples yet.</p>";
  if (dashboard.signals[0]) await loadSignalDetail(dashboard.signals[0].pool_address);
}

async function loadSignalDetail(poolAddress) {
  const detail = document.querySelector("#detail");
  const response = await fetch(`/api/signals/${encodeURIComponent(poolAddress)}`);
  if (!response.ok) throw new Error("Signal detail unavailable");
  const signal = await response.json();
  const advice = signal.advice
    ? `Entry ceiling: $${escapeHtml(signal.advice.entry_ceiling_usd)} · Max position: ${escapeHtml(signal.advice.max_position_pct)}% · Stop: ${escapeHtml(signal.advice.stop_loss_pct)}% · Take profit: ${escapeHtml(signal.advice.take_profit_pcts.join(" / "))}% · Invalidation: ${escapeHtml(signal.advice.invalidation)}`
    : "No trade advice.";
  detail.innerHTML = `<article class="detail-card"><h3>${escapeHtml(signal.pool_address)}</h3><h4>Scoring evidence</h4><ul>${signal.reasons.map((reason) => `<li>${escapeHtml(reason)}</li>`).join("")}</ul><h4>Risk flags</h4><ul>${signal.risk_flags.map((flag) => `<li>${escapeHtml(flag)}</li>`).join("") || "<li>None</li>"}</ul><h4>Advice</h4><p>${advice}</p><a href="${escapeHtml(signal.dexscreener_url)}" target="_blank" rel="noopener noreferrer">Open in DexScreener</a></article>`;
}

loadDashboard().catch((error) => { document.querySelector("#signals").textContent = error.message; });
