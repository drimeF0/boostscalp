/* Стакан (DOM): агрегация уровней, клики ЛКМ/ПКМ, режим стоп/тейк (T). */
"use strict";

const Dom = { levels: 18 };

Dom.init = function () {
  const body = document.getElementById("dom-body");
  body.addEventListener("contextmenu", (e) => e.preventDefault());
  document.getElementById("dom-agg").onchange = (e) => {
    App.aggregation = parseInt(e.target.value, 10) || 1;
  };
};

function roundTo(x, step) {
  const dec = Math.max(0, -Math.floor(Math.log10(step) + 1e-9));
  return Number(x.toFixed(Math.min(dec + 2, 8)));
}

function aggregate(levels, step, side) {
  const map = new Map();
  for (const [p, q] of levels) {
    const b = side === "bid" ? Math.floor(p / step) * step : Math.ceil(p / step) * step;
    const key = roundTo(b, step);
    map.set(key, (map.get(key) || 0) + q);
  }
  const arr = [...map.entries()];
  arr.sort((a, b) => side === "bid" ? b[0] - a[0] : a[0] - b[0]);
  return arr.slice(0, Dom.levels);
}

Dom.render = function (bids, asks) {
  const step = (App.tickSize || 0.01) * App.aggregation;
  const ab = aggregate(bids || [], step, "bid");
  const aa = aggregate(asks || [], step, "ask");
  const maxQ = Math.max(1e-9, ...ab.map((x) => x[1]), ...aa.map((x) => x[1]));

  renderSide(document.getElementById("dom-bids"), ab, maxQ, "bid", step);
  renderSide(document.getElementById("dom-asks"), aa, maxQ, "ask", step);

  const sp = document.getElementById("dom-spread");
  if (bids && bids.length && asks && asks.length) {
    const s = asks[0][0] - bids[0][0];
    sp.textContent = `спред ${fmt(s, 4)} · шаг ${roundTo(step, step)}`;
  }
};

function levelClass(price) {
  const p = App.position || {};
  const eps = (App.tickSize || 0.01) * App.aggregation / 2;
  if (p.sl != null && Math.abs(price - p.sl) <= eps) return " sl-line";
  if (p.tp != null && Math.abs(price - p.tp) <= eps) return " tp-line";
  if (p.qty && Math.abs(price - p.entry) <= eps) return " pos-line";
  return "";
}

function renderSide(container, rows, maxQ, side, step) {
  // перерисовываем только если изменилось (частые апдейты)
  const htmlKey = rows.map((r) => r[0] + ":" + r[1].toFixed(4)).join("|") +
    "|" + (App.position.sl || "") + "|" + (App.position.tp || "") + "|" + (App.position.entry || "");
  if (container.dataset.key === htmlKey) return;
  container.dataset.key = htmlKey;
  container.innerHTML = "";
  const dec = Math.max(0, -Math.floor(Math.log10(step) + 1e-9));
  for (const [price, qty] of rows) {
    const row = document.createElement("div");
    row.className = "dom-row " + side + levelClass(price);
    const depth = document.createElement("div");
    depth.className = "depth";
    depth.style.width = Math.min(100, (qty / maxQ) * 100).toFixed(1) + "%";
    const p = document.createElement("span");
    p.className = "p";
    p.textContent = price.toFixed(Math.min(dec, 8));
    const q = document.createElement("span");
    q.textContent = qty >= 1000 ? (qty / 1000).toFixed(1) + "k" : qty.toFixed(qty < 1 ? 4 : 2);
    row.appendChild(depth); row.appendChild(p); row.appendChild(q);
    row.addEventListener("mousedown", (e) => onRowClick(e, price, side));
    container.appendChild(row);
  }
}

/* ЛКМ бид=лимит buy, ЛКМ аск=market buy; ПКМ аск=лимит sell, ПКМ бид=market sell.
   С зажатым T: ПКМ — стоп/тейк по уровню, ЛКМ — отмена SL/TP. */
function onRowClick(e, price, side) {
  e.preventDefault();
  if (App.slTpArmed) {
    if (e.button === 2) send({ type: "set_sl_tp", price });
    else if (e.button === 0) send({ type: "cancel_sl_tp" });
    return;
  }
  if (e.button === 0) {           // ЛКМ — покупка
    if (side === "bid") sendOrder("buy", "limit", price);
    else sendOrder("buy", "market");
  } else if (e.button === 2) {    // ПКМ — продажа
    if (side === "ask") sendOrder("sell", "limit", price);
    else sendOrder("sell", "market");
  }
}
