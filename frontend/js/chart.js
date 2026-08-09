/* График: свечи (lightweight-charts), линии позиции/ордеров/SL-TP, маркеры сделок. */
"use strict";

const Chart = {
  chart: null,
  candleSeries: null,
  volSeries: null,
  deltaSeries: null,
  oiSeries: null,
  priceLines: [],
  markers: [],
  lastCandleTime: 0,
  drawingTool: "cursor",
  drawingStart: null,
  drawings: [],
  drawingSeq: 0,
  hoverPoint: null,
  indicators: { volume: true, delta: false, oi: false },
};

Chart.init = function () {
  const el = document.getElementById("chart");
  Chart.chart = LightweightCharts.createChart(el, {
    layout: {
      background: { color: "#0d1117" },
      textColor: "#7d8ba0",
      fontSize: 11,
    },
    grid: {
      vertLines: { color: "rgba(42,53,70,.5)" },
      horzLines: { color: "rgba(42,53,70,.5)" },
    },
    crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
    rightPriceScale: { borderColor: "#2a3546" },
    timeScale: { borderColor: "#2a3546", timeVisible: true, secondsVisible: false },
    autoSize: true,
  });

  Chart.candleSeries = Chart.chart.addCandlestickSeries({
    upColor: "#26a69a", downColor: "#ef5350",
    borderUpColor: "#26a69a", borderDownColor: "#ef5350",
    wickUpColor: "#26a69a", wickDownColor: "#ef5350",
  });
  Chart.volSeries = Chart.chart.addHistogramSeries({
    priceFormat: { type: "volume" },
    priceScaleId: "",
  });
  Chart.chart.priceScale("").applyOptions({ scaleMargins: { top: 0.85, bottom: 0 } });
  Chart.deltaSeries = Chart.chart.addHistogramSeries({
    priceScaleId: "delta", visible: false,
    priceFormat: { type: "volume" },
  });
  Chart.chart.priceScale("delta").applyOptions({
    scaleMargins: { top: 0.70, bottom: 0.16 }, borderVisible: false,
  });
  Chart.oiSeries = Chart.chart.addLineSeries({
    priceScaleId: "oi", visible: false, color: "#b56cff", lineWidth: 2,
    priceFormat: { type: "volume" }, lastValueVisible: true, priceLineVisible: false,
  });
  Chart.chart.priceScale("oi").applyOptions({
    scaleMargins: { top: 0.56, bottom: 0.31 }, borderVisible: false,
  });

  // клик по графику с зажатым T — установка стоп/тейк
  Chart.chart.subscribeClick((param) => {
    if (Chart.drawingTool !== "cursor" && param.point && param.time != null) {
      const price = Chart.candleSeries.coordinateToPrice(param.point.y);
      if (price != null) Chart.onDrawingClick(param.time, price);
      return;
    }
    if (!App.slTpArmed || !param.point) return;
    const price = Chart.candleSeries.coordinateToPrice(param.point.y);
    if (price == null) return;
    send({ type: "set_sl_tp", price });
  });
  Chart.chart.subscribeCrosshairMove((param) => {
    if (param.point && param.time != null) {
      const price = Chart.candleSeries.coordinateToPrice(param.point.y);
      Chart.hoverPoint = price == null ? null : { time: param.time, price };
    } else {
      Chart.hoverPoint = null;
    }
    Chart.renderDrawings();
  });
  Chart.chart.timeScale().subscribeVisibleLogicalRangeChange(() => Chart.renderDrawings());
  new ResizeObserver(() => Chart.renderDrawings()).observe(document.getElementById("chart-wrap"));
  Chart.initTools();
};

Chart.setHistory = function (candles) {
  if (!Chart.candleSeries) return;
  Chart.clearDrawings(true);
  Chart.oiSeries.setData([]);
  const data = candles.map((c) => ({
    time: c.time, open: c.open, high: c.high, low: c.low, close: c.close,
  }));
  const vols = candles.map((c) => ({
    time: c.time, value: c.volume,
    color: c.close >= c.open ? "rgba(38,166,154,.4)" : "rgba(239,83,80,.4)",
  }));
  const deltas = candles.map((c) => ({
    time: c.time, value: c.delta || 0,
    color: (c.delta || 0) >= 0 ? "rgba(32,180,134,.7)" : "rgba(240,82,82,.7)",
  }));
  Chart.candleSeries.setData(data);
  Chart.volSeries.setData(vols);
  Chart.deltaSeries.setData(deltas);
  Chart.markers = [];
  Chart.candleSeries.setMarkers([]);
  Chart.lastCandleTime = candles.length ? candles[candles.length - 1].time : 0;
  if (candles.length) App.lastPrice = candles[candles.length - 1].close;
  Chart.chart.timeScale().scrollToRealTime();
};

Chart.updateCandle = function (c) {
  if (!Chart.candleSeries || !c) return;
  Chart.candleSeries.update({ time: c.time, open: c.open, high: c.high, low: c.low, close: c.close });
  Chart.volSeries.update({
    time: c.time, value: c.volume,
    color: c.close >= c.open ? "rgba(38,166,154,.4)" : "rgba(239,83,80,.4)",
  });
  Chart.deltaSeries.update({
    time: c.time, value: c.delta || 0,
    color: (c.delta || 0) >= 0 ? "rgba(32,180,134,.7)" : "rgba(240,82,82,.7)",
  });
  Chart.lastCandleTime = c.time;
};

Chart.updateOi = function (m) {
  if (!Chart.oiSeries || !m.oi || !m.ts) return;
  const interval = ({ "1m": 60, "3m": 180, "5m": 300, "15m": 900,
    "30m": 1800, "1h": 3600, "4h": 14400 })[App.timeframe] || 60;
  const time = Math.floor(m.ts / 1000 / interval) * interval;
  Chart.oiSeries.update({ time, value: m.oi });
};

Chart.setIndicatorHistory = function (history) {
  if (!Chart.oiSeries) return;
  const oi = (history.oi || []).map((point) => ({
    time: Math.floor(point.ts / 1000), value: point.value,
  })).sort((a, b) => a.time - b.time);
  Chart.oiSeries.setData(oi);
};

Chart.initTools = function () {
  document.querySelectorAll(".indicator-toggle").forEach((button) => {
    button.onclick = () => {
      const name = button.dataset.indicator;
      Chart.indicators[name] = !Chart.indicators[name];
      button.classList.toggle("active", Chart.indicators[name]);
      if (name === "volume") Chart.volSeries.applyOptions({ visible: Chart.indicators[name] });
      if (name === "delta") Chart.deltaSeries.applyOptions({ visible: Chart.indicators[name] });
      if (name === "oi") Chart.oiSeries.applyOptions({ visible: Chart.indicators[name] });
    };
  });
  document.querySelectorAll(".drawing-tool").forEach((button) => {
    button.onclick = () => {
      Chart.drawingTool = button.dataset.tool;
      Chart.drawingStart = null;
      document.querySelectorAll(".drawing-tool").forEach((b) => b.classList.toggle("active", b === button));
      document.getElementById("chart").style.cursor = Chart.drawingTool === "cursor" ? "" : "crosshair";
      Chart.renderDrawings();
    };
  });
  document.getElementById("clear-drawings").onclick = () => Chart.clearDrawings(false);
};

Chart.onDrawingClick = function (time, price) {
  if (Chart.drawingTool === "horizontal") {
    Chart.drawings.push({ id: ++Chart.drawingSeq, type: "horizontal", price });
    Chart.renderDrawings();
    return;
  }
  if (!Chart.drawingStart) {
    Chart.drawingStart = { time, price };
    toast("info", "Выберите вторую точку рисунка");
    return;
  }
  const start = Chart.drawingStart;
  Chart.drawingStart = null;
  if (Chart.drawingTool === "trend") {
    Chart.drawings.push({ id: ++Chart.drawingSeq, type: "trend", start, end: { time, price } });
  } else if (Chart.drawingTool === "rectangle") {
    Chart.drawings.push({ id: ++Chart.drawingSeq, type: "rectangle", start, end: { time, price } });
  }
  Chart.renderDrawings();
};

Chart.clearDrawings = function (silent = false) {
  Chart.drawings = [];
  Chart.drawingStart = null;
  Chart.renderDrawings();
  if (!silent) toast("info", "Рисунки удалены");
};

Chart.removeDrawing = function (id) {
  Chart.drawings = Chart.drawings.filter((drawing) => drawing.id !== id);
  Chart.renderDrawings();
  toast("info", "Объект рисования удалён");
};

Chart.svg = function (name, attrs, className = "") {
  const node = document.createElementNS("http://www.w3.org/2000/svg", name);
  Object.entries(attrs).forEach(([key, value]) => node.setAttribute(key, value));
  if (className) node.setAttribute("class", className);
  return node;
};

Chart.coords = function (point) {
  if (!point) return null;
  const x = Chart.chart.timeScale().timeToCoordinate(point.time);
  const y = Chart.candleSeries.priceToCoordinate(point.price);
  return x == null || y == null ? null : { x, y };
};

Chart.appendLineDrawing = function (overlay, drawing, preview = false) {
  const a = Chart.coords(drawing.start);
  const b = Chart.coords(drawing.end);
  if (!a || !b) return;
  const color = preview ? "#8eb5ff" : "#4c8dff";
  overlay.appendChild(Chart.svg("line", { x1: a.x, y1: a.y, x2: b.x, y2: b.y,
    stroke: color, "stroke-width": 2 }, preview ? "drawing-preview" : "drawing-visible"));
  if (!preview) {
    const hit = Chart.svg("line", { x1: a.x, y1: a.y, x2: b.x, y2: b.y,
      stroke: "rgba(0,0,0,0)", "stroke-width": 14 }, "drawing-hit");
    hit.addEventListener("contextmenu", (event) => {
      event.preventDefault(); event.stopPropagation(); Chart.removeDrawing(drawing.id);
    });
    overlay.appendChild(hit);
  }
};

Chart.appendRectangleDrawing = function (overlay, drawing, preview = false) {
  const a = Chart.coords(drawing.start);
  const b = Chart.coords(drawing.end);
  if (!a || !b) return;
  const attrs = { x: Math.min(a.x, b.x), y: Math.min(a.y, b.y),
    width: Math.abs(a.x - b.x), height: Math.abs(a.y - b.y),
    fill: preview ? "rgba(142,181,255,.10)" : "rgba(76,141,255,.12)",
    stroke: preview ? "#8eb5ff" : "#4c8dff", "stroke-width": 1.5 };
  overlay.appendChild(Chart.svg("rect", attrs, preview ? "drawing-preview" : "drawing-visible"));
  if (!preview) {
    const hit = Chart.svg("rect", { ...attrs, fill: "rgba(0,0,0,0)", stroke: "rgba(0,0,0,0)",
      "stroke-width": 10 }, "drawing-hit rect-hit");
    hit.addEventListener("contextmenu", (event) => {
      event.preventDefault(); event.stopPropagation(); Chart.removeDrawing(drawing.id);
    });
    overlay.appendChild(hit);
  }
};

Chart.renderDrawings = function () {
  const overlay = document.getElementById("drawing-overlay");
  if (!overlay || !Chart.chart || !Chart.candleSeries) return;
  overlay.replaceChildren();
  const width = overlay.clientWidth;
  for (const drawing of Chart.drawings) {
    if (drawing.type === "horizontal") {
      const y = Chart.candleSeries.priceToCoordinate(drawing.price);
      if (y == null) continue;
      overlay.appendChild(Chart.svg("line", { x1: 0, y1: y, x2: width, y2: y,
        stroke: "#f0b90b", "stroke-width": 1.5, "stroke-dasharray": "7 4" }, "drawing-visible"));
      const hit = Chart.svg("line", { x1: 0, y1: y, x2: width, y2: y,
        stroke: "rgba(0,0,0,0)", "stroke-width": 14 }, "drawing-hit");
      hit.addEventListener("contextmenu", (event) => {
        event.preventDefault(); event.stopPropagation(); Chart.removeDrawing(drawing.id);
      });
      overlay.appendChild(hit);
    } else if (drawing.type === "trend") {
      Chart.appendLineDrawing(overlay, drawing);
    } else if (drawing.type === "rectangle") {
      Chart.appendRectangleDrawing(overlay, drawing);
    }
  }
  if (Chart.drawingTool === "horizontal" && Chart.hoverPoint) {
    const y = Chart.candleSeries.priceToCoordinate(Chart.hoverPoint.price);
    if (y != null) overlay.appendChild(Chart.svg("line", { x1: 0, y1: y, x2: width, y2: y,
      stroke: "#f0b90b", "stroke-width": 1.5 }, "drawing-preview"));
  } else if (Chart.drawingStart && Chart.hoverPoint && Chart.drawingTool === "trend") {
    Chart.appendLineDrawing(overlay, { start: Chart.drawingStart, end: Chart.hoverPoint }, true);
  } else if (Chart.drawingStart && Chart.hoverPoint && Chart.drawingTool === "rectangle") {
    Chart.appendRectangleDrawing(overlay, { start: Chart.drawingStart, end: Chart.hoverPoint }, true);
  }
};

Chart.addMarker = function (fill) {
  const time = Chart.lastCandleTime || Math.floor(fill.ts / 60000) * 60;
  Chart.markers.push({
    time,
    position: fill.side === "buy" ? "belowBar" : "aboveBar",
    color: fill.side === "buy" ? "#26a69a" : "#ef5350",
    shape: fill.side === "buy" ? "arrowUp" : "arrowDown",
    text: (fill.side === "buy" ? "B " : "S ") + fmt(fill.price),
  });
  Chart.markers.sort((a, b) => a.time - b.time);
  Chart.candleSeries.setMarkers(Chart.markers);
};

/* линии: вход, SL, TP, лимитные ордера */
Chart.updateLines = function () {
  if (!Chart.candleSeries) return;
  Chart.priceLines.forEach((pl) => Chart.candleSeries.removePriceLine(pl));
  Chart.priceLines = [];
  const add = (price, color, title, style) => {
    if (price == null || price <= 0) return;
    Chart.priceLines.push(Chart.candleSeries.createPriceLine({
      price, color, title, lineWidth: 1,
      lineStyle: style || LightweightCharts.LineStyle.Solid,
      axisLabelVisible: true,
    }));
  };
  const p = App.position || {};
  if (p.qty && p.qty !== 0) {
    add(p.entry, "#4c8dff", "ВХОД");
    if (p.sl) add(p.sl, "#ef5350", "SL", LightweightCharts.LineStyle.Dashed);
    if (p.tp) add(p.tp, "#26a69a", "TP", LightweightCharts.LineStyle.Dashed);
  }
  (App.orders || []).forEach((o) =>
    add(o.price, "#f0b90b", (o.side === "buy" ? "L BUY" : "L SELL"), LightweightCharts.LineStyle.Dotted));
};
