/* Настраиваемые горячие клавиши. Значения хранятся как KeyboardEvent.code. */
"use strict";

const Hotkeys = {
  defaults: {
    buy: "KeyB", sell: "KeyS", close: "KeyX", cancel: "KeyC",
    sltp: "KeyT", pause: "Space",
  },
  labels: {
    buy: "Market Buy", sell: "Market Sell", close: "Закрыть позицию",
    cancel: "Отменить ордера", sltp: "Удерживать: SL/TP", pause: "Пауза бэктеста",
  },
  bindings: {},
};

function isTyping(e) {
  const t = e.target;
  return t && (t.tagName === "INPUT" || t.tagName === "SELECT" || t.tagName === "TEXTAREA");
}

Hotkeys.load = function () {
  try {
    const saved = JSON.parse(localStorage.getItem("tt_hotkeys") || "null");
    Hotkeys.bindings = { ...Hotkeys.defaults, ...(saved || {}) };
  } catch (e) {
    Hotkeys.bindings = { ...Hotkeys.defaults };
  }
};

Hotkeys.display = function (code) {
  if (code === "Space") return "Space";
  if (code.startsWith("Key") || code.startsWith("Digit")) return code.replace(/^(Key|Digit)/, "");
  return code.replace(/^(Arrow|Numpad)/, "");
};

Hotkeys.actionFor = function (code) {
  return Object.keys(Hotkeys.bindings).find((action) => Hotkeys.bindings[action] === code);
};

Hotkeys.render = function () {
  const box = document.getElementById("hotkey-inputs");
  box.innerHTML = "";
  for (const [action, label] of Object.entries(Hotkeys.labels)) {
    const row = document.createElement("label");
    row.className = "hotkey-row";
    row.innerHTML = `<span>${label}</span><input readonly data-action="${action}" value="${Hotkeys.display(Hotkeys.bindings[action])}">`;
    const input = row.querySelector("input");
    input.addEventListener("keydown", (e) => {
      e.preventDefault(); e.stopPropagation();
      if (e.code === "Escape") { input.blur(); return; }
      if (/^Digit[1-9]$/.test(e.code)) {
        toast("warn", "Клавиши 1–9 зарезервированы для выбора объёма");
        return;
      }
      const conflict = Hotkeys.actionFor(e.code);
      if (conflict && conflict !== action) {
        toast("warn", `Клавиша уже назначена: ${Hotkeys.labels[conflict]}`);
        return;
      }
      Hotkeys.bindings[action] = e.code;
      localStorage.setItem("tt_hotkeys", JSON.stringify(Hotkeys.bindings));
      Hotkeys.render();
    });
    box.appendChild(row);
  }
  const b = Hotkeys.bindings;
  document.getElementById("hotkey-hint").textContent =
    `${Hotkeys.display(b.buy)} — купить · ${Hotkeys.display(b.sell)} — продать · ` +
    `${Hotkeys.display(b.close)} — закрыть · ${Hotkeys.display(b.cancel)} — отменить · ` +
    `${Hotkeys.display(b.sltp)} — стоп/тейк`;
};

Hotkeys.armSlTp = function () {
  if (App.slTpArmed) return;
  App.slTpArmed = true;
  document.body.style.cursor = "crosshair";
  toast("info", "Режим SL/TP: ПКМ — установить, ЛКМ — отменить");
};

Hotkeys.disarmSlTp = function () {
  App.slTpArmed = false;
  document.body.style.cursor = "";
};

Hotkeys.init = function () {
  Hotkeys.load();
  Hotkeys.render();
  document.getElementById("reset-hotkeys").onclick = () => {
    Hotkeys.bindings = { ...Hotkeys.defaults };
    localStorage.removeItem("tt_hotkeys");
    Hotkeys.render();
    toast("info", "Горячие клавиши сброшены");
  };

  document.addEventListener("keydown", (e) => {
    if (isTyping(e) || e.repeat) return;
    if (/^Digit[1-9]$/.test(e.code)) {
      const n = parseInt(e.code.slice(5), 10);
      if (App.sizes[n - 1] != null) {
        App.currentSize = App.sizes[n - 1];
        document.getElementById("cur-size").textContent = "$" + App.currentSize;
        toast("info", `Объём: $${App.currentSize}`);
      }
      return;
    }
    const action = Hotkeys.actionFor(e.code);
    if (!action) return;
    if (action === "buy") sendOrder("buy", "market");
    if (action === "sell") sendOrder("sell", "market");
    if (action === "close") send({ type: "close_position" });
    if (action === "cancel") {
      send({ type: "cancel_all" });
      toast("info", "Все лимитные ордера отменены");
    }
    if (action === "sltp") Hotkeys.armSlTp();
    if (action === "pause" && App.mode === "backtest" && App.bt.running) {
      e.preventDefault();
      send({ type: "bt_control", action: App.bt.paused ? "resume" : "pause" });
    }
  });

  document.addEventListener("keyup", (e) => {
    if (e.code === Hotkeys.bindings.sltp) Hotkeys.disarmSlTp();
  });
  window.addEventListener("blur", Hotkeys.disarmSlTp);
};
