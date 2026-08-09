/* Нижние вкладки: позиция, ордера, сделки, модель, настройки. */
"use strict";

const Tabs = {};

Tabs.init = function () {
  document.querySelectorAll(".tab").forEach((btn) => {
    btn.onclick = () => {
      document.querySelectorAll(".tab").forEach((b) => b.classList.toggle("active", b === btn));
      document.querySelectorAll(".tab-panel").forEach((p) =>
        p.classList.toggle("hidden", p.id !== "tab-" + btn.dataset.tab));
    };
  });

  // --- модель ---
  document.getElementById("train-btn").onclick = () => {
    document.getElementById("train-status").textContent = "обучение...";
    send({ type: "train_model" });
    setTimeout(() => (document.getElementById("train-status").textContent = ""), 3000);
  };

  // --- настройки: объёмы ---
  const box = document.getElementById("size-inputs");
  for (let i = 0; i < 9; i++) {
    const lab = document.createElement("label");
    lab.innerHTML = `<span class="key-cap">${i + 1}</span> <input type="number" data-i="${i}" value="${App.sizes[i]}" min="1" step="10">`;
    box.appendChild(lab);
  }
  document.getElementById("save-sizes").onclick = () => {
    const inputs = box.querySelectorAll("input");
    inputs.forEach((inp) => {
      const v = parseFloat(inp.value);
      if (v > 0) App.sizes[+inp.dataset.i] = v;
    });
    localStorage.setItem("tt_sizes", JSON.stringify(App.sizes));
    App.currentSize = App.sizes[0];
    document.getElementById("cur-size").textContent = "$" + App.currentSize;
    toast("ok", "Объёмы сохранены");
  };

  document.getElementById("apply-settings").onclick = () => {
    send({
      type: "apply_settings",
      takerFee: (parseFloat(document.getElementById("set-taker").value) || 0) / 100,
      makerFee: (parseFloat(document.getElementById("set-maker").value) || 0) / 100,
    });
    toast("ok", "Комиссии применены");
  };
  document.getElementById("reset-account").onclick = () => {
    send({
      type: "apply_settings",
      resetBalance: true,
      startBalance: parseFloat(document.getElementById("set-balance").value) || 10000,
    });
  };
};

/* ---------------- позиция ---------------- */

Tabs.renderPosition = function () {
  const tb = document.querySelector("#position-table tbody");
  const p = App.position || {};
  if (!p.qty || p.qty === 0) {
    tb.innerHTML = `<tr><td colspan="9" class="empty">Нет открытой позиции</td></tr>`;
    return;
  }
  const upnl = p.upnl != null ? p.upnl : 0;
  tb.innerHTML = `<tr>
    <td>${App.symbol}</td>
    <td class="${p.qty > 0 ? "pos" : "neg"}">${p.qty > 0 ? "LONG" : "SHORT"}</td>
    <td>${Math.abs(p.qty).toFixed(5)}</td>
    <td>${fmt(p.entry)}</td>
    <td>${fmt(App.lastPrice)}</td>
    <td class="${upnl >= 0 ? "pos" : "neg"}">${upnl >= 0 ? "+" : ""}${fmt(upnl)}</td>
    <td>${p.sl ? fmt(p.sl) : "—"}</td>
    <td>${p.tp ? fmt(p.tp) : "—"}</td>
    <td><button id="close-position-btn" ${App.closePending ? "disabled" : ""}>${App.closePending ? "Закрытие…" : "Закрыть"}</button></td>
  </tr>`;
  document.getElementById("close-position-btn").onclick = closePosition;
};

/* ---------------- ордера ---------------- */

Tabs.renderOrders = function () {
  Tabs.updateCounts();
  const tb = document.querySelector("#orders-table tbody");
  if (!App.orders || !App.orders.length) {
    tb.innerHTML = `<tr><td colspan="7" class="empty">Нет открытых ордеров</td></tr>`;
    return;
  }
  tb.innerHTML = "";
  for (const o of App.orders) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${o.id}</td>
      <td class="${o.side === "buy" ? "pos" : "neg"}">${o.side.toUpperCase()}</td>
      <td>${o.type}</td>
      <td>${fmt(o.price)}</td>
      <td>${o.qty.toFixed(5)}</td>
      <td>$${fmt(o.qty * o.price)}</td>
      <td><button>✕</button></td>`;
    tr.querySelector("button").onclick = () => send({ type: "cancel", orderId: o.id });
    tb.appendChild(tr);
  }
};

/* ---------------- сделки ---------------- */

Tabs.renderTrades = function () {
  const tb = document.querySelector("#trades-table tbody");
  if (!App.trades || !App.trades.length) {
    tb.innerHTML = `<tr><td colspan="9" class="empty">Сделок пока нет</td></tr>`;
    return;
  }
  tb.innerHTML = "";
  for (const t of App.trades) {
    const tr = document.createElement("tr");
    const d = new Date(t.exitTs);
    tr.innerHTML = `
      <td>${t.id}</td>
      <td>${t.symbol}</td>
      <td class="${t.side === "buy" ? "pos" : "neg"}">${t.side.toUpperCase()}</td>
      <td>${fmt(t.entryPrice)}</td>
      <td>${fmt(t.exitPrice)}</td>
      <td class="${t.pnl >= 0 ? "pos" : "neg"}">${t.pnl >= 0 ? "+" : ""}${fmt(t.pnl)}</td>
      <td>${t.label ? "✅" : "❌"}</td>
      <td class="trade-tags">${[...(t.entryTags || []), ...(t.exitTags || [])].map((tag) => `<span>${escapeHtml(tag)}</span>`).join("") || "—"}</td>
      <td>${d.toLocaleString()}</td>`;
    tr.className = "trade-row";
    tr.title = "Открыть карточку сделки";
    tr.onclick = () => TradeDetail.open(t.id);
    tb.appendChild(tr);
  }
};

function escapeHtml(value) {
  const el = document.createElement("span");
  el.textContent = value;
  return el.innerHTML;
}

Tabs.updateCounts = function () {
  const oc = document.getElementById("orders-count");
  const tc = document.getElementById("trades-count");
  oc.classList.toggle("hidden", !App.orders || !App.orders.length);
  oc.textContent = (App.orders || []).length;
  tc.classList.toggle("hidden", !App.tradesCount);
  tc.textContent = App.tradesCount;
};
