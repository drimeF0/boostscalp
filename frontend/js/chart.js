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
    };
  });
  document.getElementById("clear-drawings").onclick = () => Chart.clearDrawings(false);
};

Chart.onDrawingClick = function (time, price) {
  if (Chart.drawingTool === "horizontal") {
    const line = Chart.candleSeries.createPriceLine({
      price, color: "#f0b90b", lineWidth: 1,
      lineStyle: LightweightCharts.LineStyle.Dashed, axisLabelVisible: true, title: "LEVEL",
    });
    Chart.drawings.push({ type: "price", line });
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
    const series = Chart.chart.addLineSeries({
      color: "#4c8dff", lineWidth: 2, lastValueVisible: false, priceLineVisible: false,
      crosshairMarkerVisible: false,
    });
    series.setData([{ time: start.time, value: start.price }, { time, value: price }].sort((a, b) => a.time - b.time));
    Chart.drawings.push({ type: "series", series });
  } else if (Chart.drawingTool === "fib") {
    const diff = price - start.price;
    [0, .236, .382, .5, .618, .786, 1].forEach((level) => {
      const line = Chart.candleSeries.createPriceLine({
        price: start.price + diff * level, color: "rgba(181,108,255,.8)", lineWidth: 1,
        lineStyle: LightweightCharts.LineStyle.Dotted, axisLabelVisible: true,
        title: `F ${level}`,
      });
      Chart.drawings.push({ type: "price", line });
    });
  }
};

Chart.clearDrawings = function (silent = false) {
  Chart.drawings.forEach((drawing) => {
    if (drawing.type === "price") Chart.candleSeries.removePriceLine(drawing.line);
    if (drawing.type === "series") Chart.chart.removeSeries(drawing.series);
  });
  Chart.drawings = [];
  Chart.drawingStart = null;
  if (!silent) toast("info", "Рисунки удалены");
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
