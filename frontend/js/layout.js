/* Модульное рабочее пространство: видимость и размеры панелей сохраняются локально. */
"use strict";

const Layout = {
  defaults: { domWidth: 280, bottomHeight: 220, domVisible: true, bottomVisible: true },
  state: null,
  focus: false,
};

Layout.load = function () {
  try {
    const saved = JSON.parse(localStorage.getItem("tt_layout") || "null");
    return { ...Layout.defaults, ...(saved || {}) };
  } catch (e) {
    return { ...Layout.defaults };
  }
};

Layout.save = function () {
  localStorage.setItem("tt_layout", JSON.stringify(Layout.state));
};

Layout.apply = function () {
  const root = document.documentElement;
  const body = document.body;
  root.style.setProperty("--dom-width", `${Layout.state.domWidth}px`);
  root.style.setProperty("--bottom-height", `${Layout.state.bottomHeight}px`);
  body.classList.toggle("dom-hidden", !Layout.state.domVisible || Layout.focus);
  body.classList.toggle("bottom-hidden", !Layout.state.bottomVisible || Layout.focus);
  body.classList.toggle("chart-focus", Layout.focus);
  $("layout-dom").classList.toggle("active", Layout.state.domVisible && !Layout.focus);
  $("layout-bottom").classList.toggle("active", Layout.state.bottomVisible && !Layout.focus);
  $("layout-focus").classList.toggle("active", Layout.focus);
};

Layout.drag = function (handle, onMove) {
  handle.addEventListener("pointerdown", (event) => {
    event.preventDefault();
    handle.setPointerCapture(event.pointerId);
    document.body.classList.add("is-resizing");
    const move = (e) => onMove(e);
    const done = () => {
      handle.removeEventListener("pointermove", move);
      handle.removeEventListener("pointerup", done);
      handle.removeEventListener("pointercancel", done);
      document.body.classList.remove("is-resizing");
      Layout.save();
    };
    handle.addEventListener("pointermove", move);
    handle.addEventListener("pointerup", done);
    handle.addEventListener("pointercancel", done);
  });
};

Layout.init = function () {
  Layout.state = Layout.load();
  $("layout-dom").onclick = () => {
    Layout.focus = false;
    Layout.state.domVisible = !Layout.state.domVisible;
    Layout.apply(); Layout.save();
  };
  $("layout-bottom").onclick = () => {
    Layout.focus = false;
    Layout.state.bottomVisible = !Layout.state.bottomVisible;
    Layout.apply(); Layout.save();
  };
  $("layout-focus").onclick = () => { Layout.focus = !Layout.focus; Layout.apply(); };
  $("layout-reset").onclick = () => {
    Layout.state = { ...Layout.defaults };
    Layout.focus = false;
    Layout.apply(); Layout.save();
    toast("info", "Расположение панелей сброшено");
  };
  Layout.drag($("dom-resizer"), (e) => {
    const tapeWidth = document.getElementById("tape-panel").offsetWidth || 0;
    Layout.state.domWidth = Math.max(210, Math.min(520, window.innerWidth - e.clientX - tapeWidth));
    Layout.apply();
  });
  Layout.drag($("bottom-resizer"), (e) => {
    Layout.state.bottomHeight = Math.max(140, Math.min(480, window.innerHeight - e.clientY));
    Layout.apply();
  });
  Layout.apply();
};
