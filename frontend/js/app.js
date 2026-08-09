/* Ядро: WebSocket, глобальное состояние, маршрутизация сообщений. */
"use strict";

const App = {
  ws: null,
  mode: null,               // "live" | "backtest"
  symbol: "BTC/USDT",
  lastPrice: 0,
  tickSize: 0.01,
  aggregation: 1,
  sizes: [100, 250, 500, 1000, 2500, 5000, 10000, 25000, 50000],
  currentSize: 100,
  position: { qty: 0, entry: 0, sl: null, tp: null },
  orders: [],
  account: {},
  trades: [],
  tradesCount: 0,
  meta: { enabled: false, mode: "filter", threshold: 0.5, trained: false },
  bt: { running: false, paused: false, pct: 0, speed: 5, ts: null },
  slTpArmed: false,         // удерживается клавиша T
  closePending: false,
};

const $ = (id) => document.getElementById(id);

/* ---------------- WS ---------------- */

function wsConnect() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws`);
  App.ws = ws;
  ws.onmessage = (e) => routeMessage(JSON.parse(e.data));
  ws.onclose = () => {
    toast("error", "Соединение потеряно. Переподключение...");
    setTimeout(wsConnect, 2000);
  };
  ws.onopen = () => toast("ok", "Подключено к серверу");
}

function send(msg) {
  if (App.ws && App.ws.readyState === WebSocket.OPEN) {
    App.ws.send(JSON.stringify(msg));
    return true;
  }
  toast("error", "Команда не отправлена: нет соединения с сервером");
  return false;
}

function routeMessage(m) {
  switch (m.type) {
    case "state":        onState(m); break;
    case "mode":
      App.mode = m.mode;
      App.symbol = m.symbol || App.symbol;
      $("chart-symbol").textContent = App.symbol;
      document.querySelector(".panel-meta").textContent = m.mode === "backtest" ? "Replay" : "Live chart";
      break;
    case "history":      Chart.setHistory(m.candles); break;
    case "tick":         onTick(m); break;
    case "book":         App.tickSize = m.tickSize || App.tickSize; Dom.render(m.bids, m.asks); break;
    case "fill":         onFill(m.data); break;
    case "position":
      App.position = m.data;
      if (!m.data.qty) App.closePending = false;
      renderPosition(); Chart.updateLines();
      break;
    case "account":      App.account = m.data; renderAccount(); break;
    case "orders":       App.orders = m.data; Tabs.renderOrders(); Chart.updateLines(); break;
    case "trade_closed": onTradeClosed(m.trade); break;
    case "trades_count": App.tradesCount = m.count; Tabs.updateCounts(); break;
    case "trades_list":  App.trades = m.trades; Tabs.renderTrades(); break;
    case "model_status": onModelStatus(m); break;
    case "meta_verdict": onVerdict(m); break;
    case "notification": toast(m.level, m.text); break;
    case "bt_status":    App.bt = m; renderBtStatus(); break;
    case "deriv":        renderDeriv(m); break;
  }
}

function renderDeriv(m) {
  const f = document.getElementById("deriv-funding");
  f.textContent = (m.funding * 100).toFixed(4) + "%";
  f.className = m.funding >= 0 ? "pos" : "neg";
  const o = document.getElementById("deriv-oi");
  const chg = (m.oiChg1h || 0) * 100;
  o.textContent = (chg >= 0 ? "+" : "") + chg.toFixed(2) + "%";
  o.className = chg >= 0 ? "pos" : "neg";
}

/* ---------------- обработчики ---------------- */

function onState(m) {
  App.mode = m.mode;
  App.symbol = m.symbol;
  App.account = m.account;
  App.position = m.position;
  App.closePending = false;
  App.orders = m.orders;
  App.tradesCount = m.tradesCount;
  App.bt = m.bt;
  $("chart-symbol").textContent = App.symbol;
  renderAccount(); renderPosition(); Tabs.renderOrders(); Tabs.updateCounts();
  renderBtStatus();
  Chart.updateLines();
  send({ type: "get_trades" });
}

function onTick(m) {
  App.lastPrice = m.price;
  if (m.candle) Chart.updateCandle(m.candle);
  // локальный пересчёт uPnL для шапки и таблицы
  if (App.position && App.position.qty !== 0) {
    App.position.upnl = (m.price - App.position.entry) * App.position.qty;
    renderPosition();
  }
  renderAccount();
}

function onFill(f) {
  const side = f.side === "buy" ? "BUY" : "SELL";
  toast("ok", `${side} ${f.qty.toFixed(5)} @ ${fmt(f.price)} (ком. ${f.fee.toFixed(2)})`);
  Chart.addMarker(f);
}

function onTradeClosed(t) {
  const cls = t.pnl >= 0 ? "ok" : "warn";
  toast(cls, `Сделка закрыта: PnL ${t.pnl >= 0 ? "+" : ""}${t.pnl.toFixed(2)} USDT`);
  App.tradesCount++;
  Tabs.updateCounts();
  send({ type: "get_trades" });
}

function onModelStatus(m) {
  App.meta.trained = m.trained;
  const el = $("meta-status");
  if (m.trained) {
    el.textContent = `обучена (${m.nSamples} сделок` +
      (m.metrics && m.metrics.auc != null ? `, AUC ${m.metrics.auc.toFixed(3)}` : "") + ")";
    el.className = "model-on";
  } else {
    el.textContent = `не обучена (сделок: ${m.tradesCount || 0}/${m.minTrades || 20})`;
    el.className = "model-off";
  }
  // синхронизируем контролы с серверным состоянием
  if (m.enabled !== undefined) {
    App.meta.enabled = m.enabled; App.meta.mode = m.mode; App.meta.threshold = m.threshold;
    $("meta-enabled").checked = m.enabled;
    $("meta-mode").value = m.mode;
    $("meta-threshold").value = m.threshold;
  }
  const met = $("model-metrics");
  if (m.trained && m.metrics) {
    met.textContent = `Сэмплов: ${m.nSamples} · train/test: ${m.metrics.n_train}/${m.metrics.n_test} · ` +
      `AUC: ${m.metrics.auc != null ? m.metrics.auc.toFixed(3) : "—"} · ` +
      `Accuracy: ${(m.metrics.accuracy * 100).toFixed(1)}% · доля прибыльных: ${(m.metrics.pos_rate * 100).toFixed(0)}%`;
  }
}

function onVerdict(v) {
  const el = document.createElement("div");
  el.className = "verdict " + (v.accepted ? "ok" : "bad");
  const t = new Date().toLocaleTimeString();
  const side = v.side === "buy" ? "BUY" : "SELL";
  if (v.mode === "filter") {
    el.textContent = v.accepted
      ? `${t} · Фильтр пропустил ${side} (p=${v.proba.toFixed(2)})`
      : `${t} · Фильтр ОТКЛОНИЛ ${side} (p=${v.proba.toFixed(2)} < ${v.threshold})`;
  } else {
    el.textContent = v.accepted
      ? `${t} · Советник: ${side} выглядит хорошо (p=${v.proba.toFixed(2)})`
      : `${t} · Советник: ${side} ПЛОХАЯ (p=${v.proba.toFixed(2)}) — рекомендует закрыть`;
  }
  const box = $("verdicts");
  box.prepend(el);
  while (box.children.length > 12) box.lastChild.remove();
}

/* ---------------- шапка ---------------- */

function renderAccount() {
  const a = App.account;
  if (!a || a.balance === undefined) return;
  const p = App.position || {};
  const upnl = (p.qty && App.lastPrice) ? (App.lastPrice - p.entry) * p.qty : 0;
  const equity = a.balance + upnl;
  const pnl = equity - a.startBalance;
  $("acc-balance").textContent = fmt(a.balance);
  $("acc-equity").textContent = fmt(equity);
  const pnlEl = $("acc-pnl");
  pnlEl.textContent = (pnl >= 0 ? "+" : "") + fmt(pnl);
  pnlEl.className = pnl >= 0 ? "pos" : "neg";

  const badge = $("pos-badge");
  if (p.qty && p.qty !== 0) {
    badge.classList.remove("hidden");
    badge.className = p.qty > 0 ? "long" : "short";
    badge.textContent = (p.qty > 0 ? "LONG " : "SHORT ") + Math.abs(p.qty).toFixed(5);
  } else {
    badge.classList.add("hidden");
  }
}

function renderPosition() {
  Tabs.renderPosition();
  renderAccount();
}

function renderBtStatus() {
  const bt = App.bt;
  const wrap = $("bt-progress-wrap");
  if (App.mode !== "backtest") { wrap.classList.add("hidden"); return; }
  wrap.classList.remove("hidden");
  $("bt-progress-bar").style.width = (bt.pct || 0).toFixed(1) + "%";
  $("bt-time").textContent = bt.ts ? new Date(bt.ts).toISOString().slice(0, 16).replace("T", " ") : "";
  $("bt-pause-btn").classList.toggle("hidden", !bt.running);
  $("bt-stop-btn").classList.toggle("hidden", !bt.running);
  $("bt-pause-btn").textContent = bt.paused ? "▶" : "⏸";
}

/* ---------------- действия ---------------- */

function sendOrder(side, orderType, price) {
  send({ type: "order", side, orderType, price: price || null, sizeUsd: App.currentSize });
}

function closePosition() {
  if (!App.position || !App.position.qty) {
    toast("warn", "Нет открытой позиции");
    return;
  }
  if (App.closePending) return;
  App.closePending = send({ type: "close_position" });
  if (App.closePending) {
    renderPosition();
    // Не блокируем кнопку навсегда, если ответ потерялся.
    setTimeout(() => {
      if (App.closePending) { App.closePending = false; renderPosition(); }
    }, 5000);
  }
}

function sendMetaSettings() {
  send({
    type: "model_settings",
    enabled: $("meta-enabled").checked,
    mode: $("meta-mode").value,
    threshold: parseFloat($("meta-threshold").value) || 0.5,
  });
}

/* ---------------- утилиты ---------------- */

function fmt(x, dec = 2) {
  return Number(x).toLocaleString("en-US", { minimumFractionDigits: dec, maximumFractionDigits: dec });
}

function toast(level, text) {
  const el = document.createElement("div");
  el.className = "toast " + level;
  el.textContent = text;
  $("toasts").appendChild(el);
  setTimeout(() => el.remove(), 4500);
}

/* ---------------- init ---------------- */

document.addEventListener("DOMContentLoaded", () => {
  // размеры из localStorage
  try {
    const s = JSON.parse(localStorage.getItem("tt_sizes") || "null");
    if (Array.isArray(s) && s.length === 9) App.sizes = s;
  } catch (e) {}
  App.currentSize = App.sizes[0];
  $("cur-size").textContent = "$" + App.currentSize;

  try {
    const history = JSON.parse(localStorage.getItem("tt_live_history") || "null");
    if (history) {
      $("live-history-enabled").checked = history.enabled !== false;
      $("live-history-limit").value = Math.max(50, Math.min(3000, Number(history.limit) || 500));
    }
  } catch (e) {}

  Chart.init();
  Dom.init();
  Tabs.init();
  Hotkeys.init();
  Layout.init();
  initToolbar();
  wsConnect();
});

function initToolbar() {
  // режимы
  $("mode-live").onclick = () => switchModeUi("live");
  $("mode-bt").onclick = () => switchModeUi("backtest");
  $("live-connect").onclick = () => {
    const historyEnabled = $("live-history-enabled").checked;
    const historyLimit = Math.max(50, Math.min(3000, parseInt($("live-history-limit").value, 10) || 500));
    $("live-history-limit").value = historyLimit;
    localStorage.setItem("tt_live_history", JSON.stringify({ enabled: historyEnabled, limit: historyLimit }));
    send({
      type: "start_live",
      exchange: $("live-exchange").value,
      symbol: normSymbol($("live-symbol").value),
      historyLimit: historyEnabled ? historyLimit : 0,
    });
  };
  $("live-history-enabled").onchange = () => {
    $("live-history-limit").disabled = !$("live-history-enabled").checked;
  };
  $("live-history-limit").disabled = !$("live-history-enabled").checked;
  // бэктест
  $("bt-start-btn").onclick = () => {
    send({
      type: "start_backtest",
      symbol: normSymbol($("bt-symbol").value),
      start: $("bt-start").value, end: $("bt-end").value,
      speed: parseFloat($("bt-speed").value) || 5,
      dataType: $("bt-datatype").value,
    });
  };
  $("bt-pause-btn").onclick = () => send({ type: "bt_control", action: App.bt.paused ? "resume" : "pause" });
  $("bt-stop-btn").onclick = () => send({ type: "bt_control", action: "stop" });
  $("bt-speed").onchange = () => send({ type: "bt_control", action: "speed", speed: parseFloat($("bt-speed").value) || 5 });

  // мета-модель
  $("meta-enabled").onchange = sendMetaSettings;
  $("meta-mode").onchange = sendMetaSettings;
  $("meta-threshold").onchange = sendMetaSettings;
}

function switchModeUi(mode) {
  $("mode-live").classList.toggle("active", mode === "live");
  $("mode-bt").classList.toggle("active", mode === "backtest");
  $("live-controls").classList.toggle("hidden", mode !== "live");
  $("bt-controls").classList.toggle("hidden", mode !== "backtest");
}

function normSymbol(s) {
  s = (s || "").toUpperCase().trim();
  if (s && !s.includes("/")) {
    if (s.endsWith("USDT")) s = s.slice(0, -4) + "/USDT";
    else if (s.endsWith("USDC")) s = s.slice(0, -4) + "/USDC";
  }
  return s || "BTC/USDT";
}
