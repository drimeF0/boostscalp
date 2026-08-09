/* Карточка сделки: графический контекст, CatBoost-фичи и пользовательские теги. */
"use strict";

const TradeDetail = { chart: null, series: null, trade: null };

TradeDetail.settings = function () {
  return {
    prefixMinutes: Math.max(0, Math.min(240, parseInt($("trade-prefix").value, 10) || 0)),
    suffixMinutes: Math.max(0, Math.min(240, parseInt($("trade-suffix").value, 10) || 0)),
  };
};

TradeDetail.init = function () {
  try {
    const saved = JSON.parse(localStorage.getItem("tt_trade_context") || "null");
    if (saved) {
      $("trade-prefix").value = saved.prefixMinutes ?? 30;
      $("trade-suffix").value = saved.suffixMinutes ?? 15;
    }
  } catch (e) {}
  $("save-trade-context").onclick = () => {
    localStorage.setItem("tt_trade_context", JSON.stringify(TradeDetail.settings()));
    toast("ok", "Настройки контекста сохранены");
  };
  document.querySelectorAll("[data-close-detail]").forEach((el) => el.onclick = TradeDetail.close);
  $("save-trade-tags").onclick = TradeDetail.saveTags;
  document.addEventListener("keydown", (e) => {
    if (e.code === "Escape" && !$("trade-detail-modal").classList.contains("hidden")) TradeDetail.close();
  });
};

TradeDetail.open = function (tradeId) {
  const settings = TradeDetail.settings();
  $("trade-detail-modal").classList.remove("hidden");
  $("trade-detail-title").textContent = `Сделка #${tradeId}`;
  $("trade-detail-summary").innerHTML = `<span class="dim">Загрузка…</span>`;
  send({ type: "get_trade_detail", tradeId, ...settings });
};

TradeDetail.close = function () {
  $("trade-detail-modal").classList.add("hidden");
};

TradeDetail.render = function (trade) {
  TradeDetail.trade = trade;
  const sideClass = trade.side === "buy" ? "pos" : "neg";
  $("trade-detail-title").textContent = `${trade.symbol} · сделка #${trade.id}`;
  $("trade-detail-summary").innerHTML = `
    <div><span>Сторона</span><b class="${sideClass}">${trade.side.toUpperCase()}</b></div>
    <div><span>Вход</span><b>${fmt(trade.entryPrice, 6)}</b></div>
    <div><span>Выход</span><b>${fmt(trade.exitPrice, 6)}</b></div>
    <div><span>Количество</span><b>${Number(trade.qty).toFixed(5)}</b></div>
    <div><span>PnL</span><b class="${trade.pnl >= 0 ? "pos" : "neg"}">${trade.pnl >= 0 ? "+" : ""}${fmt(trade.pnl)}</b></div>
    <div><span>Комиссия</span><b>${fmt(trade.fee)}</b></div>`;
  $("trade-entry-tags").value = (trade.entryTags || []).join(", ");
  $("trade-exit-tags").value = (trade.exitTags || []).join(", ");
  $("trade-notes").value = trade.notes || "";
  TradeDetail.renderFeatures(trade.features || {});
  TradeDetail.renderChart(trade);
};

TradeDetail.renderFeatures = function (features) {
  const box = $("trade-feature-grid");
  const entries = Object.entries(features);
  box.innerHTML = entries.length ? entries.map(([key, value]) => `
    <div><span>${escapeHtml(key)}</span><b>${typeof value === "number" ? Number(value).toLocaleString("en-US", { maximumFractionDigits: 6 }) : escapeHtml(String(value))}</b></div>
  `).join("") : `<div class="dim">Фичи отсутствуют: для расчёта требуется минимум 30 свечей до входа.</div>`;
};

TradeDetail.ensureChart = function () {
  if (TradeDetail.chart) return;
  TradeDetail.chart = LightweightCharts.createChart($("trade-detail-chart"), {
    autoSize: true,
    layout: { background: { color: "#0a0f16" }, textColor: "#7d8ba0", fontSize: 11 },
    grid: { vertLines: { color: "rgba(42,53,70,.45)" }, horzLines: { color: "rgba(42,53,70,.45)" } },
    rightPriceScale: { borderColor: "#2a3546" },
    timeScale: { borderColor: "#2a3546", timeVisible: true, secondsVisible: false },
  });
  TradeDetail.series = TradeDetail.chart.addCandlestickSeries({
    upColor: "#20b486", downColor: "#f05252", borderVisible: false,
    wickUpColor: "#20b486", wickDownColor: "#f05252",
  });
};

TradeDetail.renderChart = function (trade) {
  const candles = trade.candles || [];
  const empty = $("trade-chart-empty");
  empty.classList.toggle("hidden", candles.length > 0);
  $("trade-context-label").textContent = `${candles.length} свечей`;
  if (!candles.length) return;
  TradeDetail.ensureChart();
  TradeDetail.series.setData(candles.map((c) => ({
    time: c.time, open: c.open, high: c.high, low: c.low, close: c.close,
  })));
  const nearestTime = (ts) => candles.reduce((best, c) =>
    Math.abs(c.time * 1000 - ts) < Math.abs(best * 1000 - ts) ? c.time : best, candles[0].time);
  TradeDetail.series.setMarkers([
    { time: nearestTime(trade.entryTs), position: trade.side === "buy" ? "belowBar" : "aboveBar",
      color: "#4c8dff", shape: trade.side === "buy" ? "arrowUp" : "arrowDown", text: `Вход ${fmt(trade.entryPrice, 6)}` },
    { time: nearestTime(trade.exitTs), position: trade.side === "buy" ? "aboveBar" : "belowBar",
      color: trade.pnl >= 0 ? "#20b486" : "#f05252", shape: "circle", text: `Выход ${fmt(trade.exitPrice, 6)}` },
  ]);
  TradeDetail.chart.timeScale().fitContent();
};

TradeDetail.saveTags = function () {
  if (!TradeDetail.trade) return;
  const parse = (value) => value.split(",").map((tag) => tag.trim()).filter(Boolean).slice(0, 20);
  send({
    type: "update_trade_tags", tradeId: TradeDetail.trade.id,
    entryTags: parse($("trade-entry-tags").value),
    exitTags: parse($("trade-exit-tags").value),
    notes: $("trade-notes").value,
    ...TradeDetail.settings(),
  });
};
