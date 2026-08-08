/* Горячие клавиши: 1-9 объёмы, B/S — рыночные ордера, X — закрыть, C — отменить,
   T (удерживать) — режим стоп/тейк, Пробел — пауза бэктеста. */
"use strict";

const Hotkeys = {};

function isTyping(e) {
  const t = e.target;
  return t && (t.tagName === "INPUT" || t.tagName === "SELECT" || t.tagName === "TEXTAREA");
}

Hotkeys.init = function () {
  document.addEventListener("keydown", (e) => {
    if (isTyping(e) || e.repeat) return;

    // 1..9 — выбор объёма
    if (e.code.startsWith("Digit")) {
      const n = parseInt(e.code.slice(5), 10);
      if (n >= 1 && n <= 9 && App.sizes[n - 1] != null) {
        App.currentSize = App.sizes[n - 1];
        document.getElementById("cur-size").textContent = "$" + App.currentSize;
        toast("info", `Объём: $${App.currentSize}`);
        return;
      }
    }

    switch (e.code) {
      case "KeyB":
        sendOrder("buy", "market");
        break;
      case "KeyS":
        sendOrder("sell", "market");
        break;
      case "KeyX":
        send({ type: "close_position" });
        break;
      case "KeyC":
        send({ type: "cancel_all" });
        toast("info", "Все лимитные ордера отменены");
        break;
      case "KeyT":
        if (!App.slTpArmed) {
          App.slTpArmed = true;
          document.body.style.cursor = "crosshair";
          toast("info", "Режим SL/TP: ПКМ — установить, ЛКМ — отменить");
        }
        break;
      case "Space":
        if (App.mode === "backtest" && App.bt.running) {
          e.preventDefault();
          send({ type: "bt_control", action: App.bt.paused ? "resume" : "pause" });
        }
        break;
    }
  });

  document.addEventListener("keyup", (e) => {
    if (e.code === "KeyT") {
      App.slTpArmed = false;
      document.body.style.cursor = "";
    }
  });

  // подстраховка: если фокус потерян — сбросить режим T
  window.addEventListener("blur", () => {
    App.slTpArmed = false;
    document.body.style.cursor = "";
  });
};
