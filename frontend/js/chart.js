/* График: свечи (lightweight-charts), линии позиции/ордеров/SL-TP, маркеры сделок. */
"use strict";

const Chart = {
  chart: null,
  candleSeries: null,
  volSeries: null,
  priceLines: [],
  markers: [],
  lastCandleTime: 0,
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

  // клик по графику с зажатым T — установка стоп/тейк
  Chart.chart.subscribeClick((param) => {
    if (!App.slTpArmed || !param.point) return;
    const price = Chart.candleSeries.coordinateToPrice(param.point.y);
    if (price == null) return;
    send({ type: "set_sl_tp", price });
  });
};

Chart.setHistory = function (candles) {
  if (!Chart.candleSeries) return;
  const data = candles.map((c) => ({
    time: c.time, open: c.open, high: c.high, low: c.low, close: c.close,
  }));
  const vols = candles.map((c) => ({
    time: c.time, value: c.volume,
    color: c.close >= c.open ? "rgba(38,166,154,.4)" : "rgba(239,83,80,.4)",
  }));
  Chart.candleSeries.setData(data);
  Chart.volSeries.setData(vols);
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
  Chart.lastCandleTime = c.time;
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
